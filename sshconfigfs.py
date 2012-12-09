#!/usr/bin/env python
# FUSE filesystem to build SSH config file dynamically.
# Mark Hellewell <mark.hellewell@gmail.com>
from errno import ENOENT
import glob
#import logging
import os
from stat import S_IFDIR, S_IFREG
#from sys import argv, exit
import threading
from time import sleep, time

from fuse import FUSE, FuseOSError, Operations, LoggingMixIn

#logger = logging.getLogger()
#logger.setLevel(logging.INFO)

configLock = threading.Lock()


class SSHConfigFS(LoggingMixIn, Operations):
    """A simple FUSE filesystem which dynamically builds a config file
    for ssh.
    """
    def __init__(self, configd_dir):
        now = time()
        # configd_dir is the directory to be watched by
        # self.dir_poller from a separate thread.
        self.configd_dir = configd_dir
        # initialise the list of "files". '/' is mandatory. '/config'
        # is where our combined ssh config lives.
        self.files = {
            '/': dict(st_mode=(S_IFDIR | 0550), st_uid=os.getuid(),
                      st_gid=os.getgid(), st_nlink=2, st_ctime=now,
                      st_mtime=now, st_atime=now),
            '/config': dict(st_mode=(S_IFREG | 0440),
                            st_uid=os.getuid(),
                            st_gid=os.getgid(), st_size=0, st_nlink=1,
                            st_ctime=now, st_mtime=now, st_atime=now)
            }
        self.ssh_config = ''
        # we just started up, so generate the ssh config right now.
        self.generate_config()

    def getattr(self, path, fh=None):
        if path not in self.files:
            raise FuseOSError(ENOENT)
        with configLock:
            return self.files[path]

    def read(self, path, size, offset, fh):
        # returns the contents of the '/config' "file", and updates
        # its st_atime.
        if path != '/config':
            raise FuseOSError(ENOENT)
        with configLock:
            self.files['/config']['st_atime'] = time()
            return self.ssh_config[offset:offset + size]

    def readdir(self, path, fh):
        # '.' and '..' must be returned here.  We add 'config', since
        # that's the only other "file" in our filesystem!
        return ['.', '..', 'config']

    def init(self, arg):
        # start the thread which polls configd_dir for changes to
        # contained files, which event triggers config file rebuild.
        t = threading.Thread(target=self.dir_poller)
        t.start()

    #
    # none-FUSE methods, below
    #

    def dir_poller(self):
        """Not part of the FUSE API, this polls the configd_dir for a
        changed mtime.  A changed mtime triggers rebuilding of the
        combined config.

        This is started as a thread from within the init (not
        __init__) method.
        """
        orig_mod_timestamp = os.stat(self.configd_dir).st_mtime
        while True:
            sleep(0.5)
            try:
                now_mod_timestamp = os.stat(self.configd_dir).st_mtime
            except OSError:
                # TODO couldn't get the mtime of the configd_dir!
                # wtf!  I think it's time to exit cleanly?
                continue
            else:
                if now_mod_timestamp != orig_mod_timestamp:
                    # configd_dir has seen changes (its mtime has
                    # changed), so it's time to generate new config and
                    # save the new timestamp for later comparisons.
                    self.generate_config()
                    orig_mod_timestamp = now_mod_timestamp

    def generate_config(self):
        """Not part of the FUSE API, this combines files from
        configd_dir into a single config "file".

        It uses shell style "globbing" of files, whose names start
        with a number, to allow control of the order in which config
        chunks are included in the final combined output.

        e.g. the directory referenced by configd_dir might contain
        files named:

            01_start
            05_tunnels
            10_workhosts
            30_personalhosts

        The generated ssh config would thus contain the contents of
        these files, in the order in which they appear above.

        An underscore in the name is not necessary for the file to be
        included in final output, only that the name start with a
        number.
        """
        # use shell style globbing, to allow control of the order in
        # which config chunks are included in the final output.
        new_ssh_config = ''
        for conf_file in glob.iglob("{}/[0-9]*".format(self.configd_dir)):
            try:
                new_ssh_config += file(conf_file, 'r').read()
            except IOError as exc:
                # TODO replace print with logger
                print "IOError ({0}) while tring to read {1}: {2}".format(
                    exc.errno, conf_file, exc.strerror)
                continue
            except Exception as exc:
                # TODO replace print with logger, and work out what to
                # display from the exception caught.
                print "Unexpected exception: {}".format(exc)
                continue
            else:
                # TODO replace print with logger
                print "{} was included".format(conf_file)

        with configLock:
            # update content and size
            self.ssh_config = new_ssh_config
            self.files['/config']['st_size'] = len(self.ssh_config)

            # update mtime and atime of '/config' and '/'
            now = time()
            for attr in ('st_mtime', 'st_atime'):
                self.files['/config'][attr] = now
                self.files['/'][attr] = now

    # def destroy(self, path):
    #     pass


if __name__ == '__main__':
    # TODO should take arguments for: user, config.d location, and?

    # user's .ssh directory, to be used to automatically setup a
    # symlink to dynamically generated config.
    ssh_dir = os.path.join(os.path.expanduser('~'), '.ssh')

    # directory containing ssh config chunks
    configd_dir = os.path.join(ssh_dir, 'config.d')
    if not os.path.exists(configd_dir):
        os.mkdir(configd_dir)

    # where our filesystem will be mounted
    mountpoint = os.path.join(os.path.expanduser('~'), '.sshconfigfs')
    if not os.path.exists(mountpoint):
        os.mkdir(mountpoint)

    fuse = FUSE(SSHConfigFS(configd_dir), mountpoint, foreground=True)
