============
backup-utils
============

A pair of Python 3 scripts that read from backup definitions and invoke
``tar`` or ``rsync`` to create archives or sync files with a destination.


===========
``tarf.py``
===========

Create ``tar`` archives according to patterns read from files on the command
line, then optionally compress them. Each file will create a single archive in
the current directory or a target directory given by the ``-t`` option. The
archive will, by default, take the name of the corresponding input file
without the suffix (if any), and with ``.tar`` appended.

Each line is read as a globbing pattern that describes one or more files or
directories to be added to the tar file in place. Tilde and variable expansion
is performed on each pattern before pathname expansion. The file may contain
comments, which begin with a ``#`` and continue to the end of the line. To
include a ``#`` in the pattern, escape it with a backslash or put it inside a
double-quoted string. To include a ``"`` in the pattern, escape it with a
backslash. The pattern is otherwise literal (including internal whitespace).

If a line starts with ``@``, lines from files matching the pattern are read as
if they were in the current input file.

If a pattern is prefixed with the "copy" character (``%``), all matching files
will first be copied to a temporary directory in the target directory, which
will then be added to the tar file and removed. The temp directory would take
the name of the corresponding input file without the suffix unless a comment
starting with ``%`` precedes the file list. For example, if the input file
``configs.def`` contained::

    % ~/.vimrc

    # %bash config
    % ~/.bashrc

    # %uzbl stuff
    % $XDG_DATA_HOME/uzbl/scripts/

then ``.vimrc`` would be copied to a new temporary directory ``configs`` in
the target directory, ``.bashrc`` to ``bash``, and the directory ``scripts``
to ``uzbl``.

Implied directories can be specified in the pattern to preserve directory
structure. This is done with the first ``/./`` marker in the file pattern.
Also, any directory with a globbing pattern and all its children are
automatically implied. Otherwise, only the path's basename is implied. So for
example, the first two patterns will preserve the parent directory containing
``bar``, while the third will only add ``bar`` to the tar file::

    ~/1/./foo/bar
    ~/2/*/bar
    ~/3/foo/bar


Usage
=====
::

  tarf.py [-t DIRECTORY] [-a FMT] [-LHfvne] [-zj] FILE...

Options
=======
::

  --version             show program's version number and exit
  -h, --help            show this help message and exit
  -?, --usage           show a brief usage string and exit
  -t DIRECTORY, --target=DIRECTORY
                        create archives in DIRECTORY (default is the current
                        directory)
  -a FMT, --archive=FMT
                        format string for the archive name, on which variable
                        and strftime substitution will be performed; the
                        pattern "{}" will be replaced with the name of the
                        corresponding input file, without the suffix
  -L, --dereference     follow all symbolic links
  -H                    try to follow any symbolic links specified by a file
                        pattern (and only those links)
  -f, --force           overwrite files without confirmation
  -v, --verbose         print messages
  -n, --simulate        read input files, but don't write to disk
  -e, --extglob         enable bash extended globbing (requires `extglob' in
                        $PATH)
  -z, --gzip            compress archives with gzip
  -j, --bzip2           compress archives with bzip2


===========
``yarf.py``
===========

Read file patterns from files or standard input and invoke ``rsync`` to
transfer files to a destination given by the ``-t`` option. Each line is read
as a globbing pattern for files or directories, or a descriptor for a remote
source to be transferred.

Tilde and variable expansion is performed on each pattern before pathname
expansion. The file may contain comments, which begin with a ``#`` and
continue to the end of the line. To include a ``#`` in the pattern, escape it
with a backslash or put it inside a double-quoted string. To include a ``"``
in the pattern, escape it with a backslash. The pattern is otherwise literal
(including internal whitespace).

If a line starts with ``@``, lines from files matching the pattern are read as
if they were in the current input file.

Implied directories can be specified in the pattern to preserve directory
structure. This is done with the first ``/./`` marker in the file pattern, as
in newer versions of ``rsync`` (see the ``--relative`` ``rsync`` option).
Also, any directory with a globbing pattern and all its children are
automatically implied. Otherwise, only the path's basename is implied. So for
example, the first two patterns will preserve the parent directory containing
``bar``, while the third will only transfer ``bar`` to the destination::

    ~/1/./foo/bar
    ~/2/*/bar
    ~/3/foo/bar

If a line starts with the "purge" character (``!``), all files matching the
implied part of the pattern are deleted from the destination unless they exist
in ``SRC``. For example, if we have
::

    ! ~/./.config/*.conf
    ! ~/foo/*/bar

and ``DEST`` is ``/bak``, then files matching ``/bak/.config/*.conf`` and
``/bak/*/bar`` would be purged. This operation is only attempted if ``DEST``
is local.

Implied directories are also used for changing the source directory with the
``-s`` option. If ``SRC`` is set to ``/mnt``, then ``/mnt/.config/*.conf`` and
``/mnt/*/bar`` would be transferred instead.


Usage
=====
::

  yarf.py [-t DEST] [-s SRC] [-o RSYNC_OPTS]... [-dzn] [-LHrve] [FILE]...

Options
=======
::

  --version             show program's version number and exit
  -h, --help            show this help message and exit
  -?, --usage           show a brief usage string and exit
  -t DEST, --target=DEST
                        set the rsync destination to DEST (default is the
                        current directory)
  -s SRC, --source=SRC  prepend SRC to the implied part of each file pattern
                        and use that instead (i.e. transfer files rooted in
                        SRC)
  -o RSYNC_OPTS, --options=RSYNC_OPTS
                        options to pass to rsync in addition to the defaults
                        ("-a"); for example: --options="-cu --exclude=.git"
  -d, --delete          pass the "--delete" option to rsync
  -z, --compress        pass the "--compress" option to rsync
  -n, --simulate        if not --verbose, pass the "--dry-run" option to rsync
                        and print the file list; otherwise, skip running rsync
                        and only print output from yarf.py (in both cases, no
                        files would be purged from the destination)
  -L, --dereference     follow all symbolic links
  -H                    try to follow any symbolic links specified by a file
                        pattern (and only those links)
  -r, --remote          allow patterns to describe remote sources
  -v, --verbose         print messages from yarf.py (use --options for rsync
                        verbosity)
  -e, --extglob         enable bash extended globbing (requires `extglob' in
                        $PATH)


======
Author
======

David Liang (bmdavll at gmail.com)

