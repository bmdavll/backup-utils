#!/usr/bin/env python3

#########################################################################
#
#   Copyright 2009 David Liang
#
#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation, either version 3 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
#   Revisions:
#   2009-04-15  File created
#   2009-04-17  Documentation
#
#########################################################################

import sys, os, signal, re
from os import path
from glob import glob
from subprocess import Popen, PIPE
from time import strftime

__version__ = "0.5"
__usage__ = "Usage: %prog [-t DIRECTORY] [-a FMT] [-LHfvne] [-zj] FILE..."
__doc__ = """
Create tar archives according to patterns read from files on the command line,
then optionally compress them. Each file will create a single archive in the
current directory or a target directory given by the "-t" option. The archive
will, by default, take the name of the corresponding input file without the
suffix (if any), and with "%(_tar_ext)s" appended (see the "-a" option for the
alternative).

Each line is read as a globbing pattern that describes one or more files or
directories to be added to the tar file in place. Tilde and variable expansion
is performed on each pattern before pathname expansion. The file may contain
comments, which begin with a '#' and continue to the end of the line. To
include a '#' in the pattern, escape it with a backslash or put it inside a
double-quoted string. To include a '"' in the pattern, escape it with a
backslash. The pattern is otherwise literal (including internal whitespace).

If a line starts with '%(_link_chr)c', lines from files matching the pattern are read as if
they were in the current input file.

If a pattern is prefixed with the "copy" character ('%(_copy_chr)c'), all matching files
will first be copied to a temporary directory in the target directory, which
will then be added to the tar file and removed. The temp directory would take
the name of the corresponding input file without the suffix unless a comment
starting with '%(_copy_chr)c' precedes the file list. For example, if the input file
"configs.def" contained

    %(_copy_chr)c ~/.vimrc
    # %(_copy_chr)c bash config
    %(_copy_chr)c ~/.bashrc
    # %(_copy_chr)c uzbl stuff
    %(_copy_chr)c $XDG_DATA_HOME/uzbl/scripts/

then .vimrc would be copied to a new temporary directory "configs" in the
target directory, .bashrc to "bash", and the directory scripts to "uzbl".

Implied directories can be specified in the pattern to preserve directory
structure. This is done with the first "/./" marker in the file pattern. Also,
any directory with a globbing pattern and all its children are automatically
implied. Otherwise, only the path's basename is implied. So for example, the
first two patterns will preserve the parent directory containing "bar", while
the third will only add "bar" to the tar file:

    ~/1/./foo/bar
    ~/2/*/bar
    ~/3/foo/bar
"""

__debugging__ = False

def Debug(*args, sep=' ', file=sys.stderr):
    if __debugging__:
        ProgPrint(*args, name="db", sep=sep, file=file)

def ProgPrint(*args, name=None, sep=' ', end='\n', file=sys.stdout):
    if name is None:
        name = __prog__
    if len(args) == 0:
        print(file=file, end=end)
    else:
        print(name+': '+sep.join(map(str, args)), end=end, file=file)

def TestPrint(condition, *args, sep=' ', end='\n', file=sys.stdout):
    if condition:
        ProgPrint(*args, sep=sep, end=end, file=file)

def PrintError(*args, sep=': ', end='\n', file=sys.stderr):
    pargs = []
    for arg in args:
        if arg is not None and arg != '':
            pargs.append(arg)
    if pargs:
        ProgPrint(*pargs, sep=sep, end=end, file=file)


class Exit(Exception):

    def __init__(self, status, *args):
        self.status = status
        self.args = args

class Fatal(Exit):

    def __init__(self, *args):
        status = 66

        if len(args) > 0 and isinstance(args[0], int):
            status = args[0]
            args = args[1:]

        if len(args) == 0:
            args = ("fatal error",)

        super().__init__(status, *args)


def handler(signum, frame):
    msg = None

    if signum:
        for signame in ("SIGINT", "SIGQUIT", "SIGABRT"):
            if signum == getattr(signal, signame, None):
                msg="aborted"
        for signame in ("SIGHUP", "SIGTERM"):
            if signum == getattr(signal, signame, None):
                msg="terminated"

    if msg: raise Exit(signum, msg)


def shortPath(path):
    return _re_home.sub('~', path)

def walkRemove(top):
    if path.isfile(top) or path.islink(top):
        os.remove(top)
    elif path.isdir(top):
        for root, dirs, files in os.walk(top, topdown=False):
            for name in files:
                os.remove(path.join(root, name))
            for name in dirs:
                os.rmdir(path.join(root, name))
        os.rmdir(top)

def confirmRemove(file):
    if path.islink(file):
        filetype = "link"
    elif path.isfile(file):
        filetype = "file"
    elif path.isdir(file):
        filetype = "directory"
    else:
        return False

    if _force:
        return True

    PrintError("overwrite existing", filetype, shortPath(file) + "? [y/N] ",
               sep=' ', end='')
    try:
        reply = input()
    except EOFError:
        reply = ''
    if reply.startswith('y') or reply.startswith('Y'):
        return True
    else:
        return False

def safeRemove(file, force=False):
    if not path.exists(file):
        return True

    if force or confirmRemove(file):
        try:
            walkRemove(file)
            return True
        except OSError as e:
            PrintError(e.filename, e.strerror)
            updateStatus(1)
            return False
    else:
        return False

def runProc(argv, stdout=None, stderr=None, input=None, text=True,
            ignore_code=False):
    try:
        proc = Popen(argv, stdout=stdout, stderr=stderr,
                     stdin=(None if input is None else PIPE),
                     universal_newlines=text)
    except OSError:
        raise Fatal(127, argv[0], "command not found")

    _children.add(proc)
    if type(input) is str:
        input = input.encode()
    try:
        out, err = proc.communicate(input)
    except OSError:
        out, err = None, None
    _children.remove(proc)

    code = proc.poll()
    if code != 0 and not ignore_code:
        updateStatus(code)

    return out, err, code

class FileCollection:

    def __init__(self, name):
        if name:
            self.name = name
            self.path = path.join(_dest, self.name)
        self.status = None # not committed to disk
                    # False: tried to commit but failed
                    # True:  committed (partially or fully)
        self.queues = {}

    def add(self, srcList, base, follow=False):
        if not srcList:
            return

        try:
            self.queues[(base, follow)] += srcList
        except KeyError:
            self.queues[(base, follow)] = srcList

    def prep(self):
        if not safeRemove(self.path):
            self.status = False
        return self.status is not False

    def commit(self):
        if self.status is not None:
            return False
        elif not self.queues:
            return True
        elif not self.prep():
            return False

        working_dir = os.getcwd()

        try:
            return self.checkedCommit()

        except OSError as e:
            PrintError(e.filename, e.strerror)
            updateStatus(1)
            return False

        finally:
            os.chdir(working_dir)

    def checkedCommit(self):
        return NotImplemented

    def remove(self):
        if self.status is not True:
            return True
        elif safeRemove(self.path, force=True):
            return True
        else:
            return False

class Archive(FileCollection):

    def __init__(self, base, ext):
        super().__init__(base + _tar_ext)
        if ext and ext != _tar_ext:
            self.final_name = base + ext
        else:
            self.final_name = base + _tar_ext
            if _compress:
                self.final_name += _compressed_exts[_compress[0]]

    def checkedCommit(self):
        TestPrint(_verbose, "creating", self.name, "in", shortPath(_target))

        for base, follow in self.queues.keys():
            os.chdir(base)

            options = [ '--dereference' ] if follow else []
            srcList = self.queues[(base, follow)]

            if self.status is None:
                code = runProc(_tar_create + [ self.path ] + options + srcList)[2]
                if path.exists(self.path):
                    self.status = True
                else:
                    self.status = False
            else:
                code = runProc(_tar_append + [ self.path ] + options + srcList)[2]

            if code != 0:
                return False

        return True

    def compressAndReplace(self):
        if self.status is not True:
            return True

        if _compress:
            new_path = path.join(_dest, self.final_name)
            if not safeRemove(new_path):
                return False

            TestPrint(_verbose, "compressing with", _compress[0])

            try:
                with open(new_path, 'wb') as compressed:
                    code = runProc(_compress + [ self.path ],
                                   stdout=compressed, text=False)[2]
                    if self.remove():
                        self.path = new_path
                        self.name = self.final_name
                    else:
                        safeRemove(new_path, force=True)
                        return False

                    if code != 0:
                        return False
                    else:
                        return True

            except IOError as e:
                PrintError(e.filename, e.strerror)
                updateStatus(1)
                return False

        elif self.name != self.final_name:
            new_path = path.join(_dest, self.final_name)
            if not safeRemove(new_path):
                return False

            try:
                os.rename(self.path, new_path)
                self.path = new_path
                self.name = self.final_name
                return True

            except OSError:
                updateStatus(1)
                return False

        else:
            return True

class Tempdir(FileCollection):

    def prep(self):
        if not super().prep():
            return False

        try:
            os.mkdir(self.path)
            self.status = True
        except OSError as e:
            PrintError("error creating temporary directory",
                       e.filename, e.strerror)
            updateStatus(1)
            self.status = False

        return self.status

    def checkedCommit(self):
        TestPrint(_verbose, "copying files to", shortPath(self.path))

        for base, follow in self.queues.keys():
            os.chdir(base)

            code = runProc( _cp_default + [ self.path ] +
                            ( [ '--dereference' ] if follow else [] ) +
                            self.queues[(base, follow)] )[2]
            if code != 0:
                return False

        return True


def parseLine(line):
    for char in _reserved_flags:
        if char in line:
            match = _re_leading_flags.match(line)
            leading_flags = match.group('flags')
            line = match.group('line')
            break
    else:
        leading_flags = ''
    if '#' in line:
        comment = _re_comments.sub(r'\g<comment>', line, 1).lstrip('#').lstrip()
        match = _re_tempdir.match(comment)
        if match:
            setTempdir(match.group('tempdir'))
        line = _re_comments.sub(r'\g<repl>', line, 1).rstrip()
    line = path.expandvars(path.expanduser(line))
    if '"' in line:
        line = _re_quotes.sub(r'\g<repl1>\g<repl2>', line)
        if _re_bare_quote.search(line):
            PrintError("syntax", "unterminated quote", line)
            updateStatus(1)
            return ''
    if '\\' in line:
        line = _re_escaped.sub(r'\g<repl>', line)
    if os.sep * 2 in line:
        line = _re_repeated_sep.sub(os.sep, line)
    return leading_flags, _re_repeated_relative.sub(r'\g<repl>', line)

def processLine(line):
    flags, pattern = parseLine(line)

    if not pattern:
        return False

    link = _link_chr in flags
    copy = _copy_chr in flags

    if link:
        try:
            link_path = path.join(_filedir, pattern)
            links = _glob(link_path)
            if not links:
                links.append(link_path)
            for link in links:
                with open(link) as file:
                    readLines(file, path.dirname(link))
            return True
        except IOError as e:
            PrintError(e.filename, e.strerror)
            updateStatus(1)
            return False

    if _relative_pat not in pattern:
        implied_pat = path.basename(pattern)
        if not implied_pat:
            implied_pat = path.basename(path.dirname(pattern)) + os.sep
            implied_pat = implied_pat.lstrip(os.sep)
    else:
        implied_pat = _re_implied_part.sub(r'\g<repl>', pattern)

    glob_match = _re_glob_part.search(pattern)

    if glob_match:
        glob_pat = glob_match.group().lstrip(os.sep)
    else:
        glob_pat = ''

    implied_pat = max(implied_pat, glob_pat, key=len)
    abspath = path.abspath(pattern[ : -len(implied_pat)])

    if path.isdir(abspath):
        os.chdir(abspath)
    else:
        PrintError("no matches", pattern)
        updateStatus(1)
        return False

    if not implied_pat:
        implied_pat = '.'

    globList = _glob(implied_pat)

    if not globList:
        PrintError("no matches", pattern)
        updateStatus(1)
        return False

    if _deref == 'L':
        if _verbose:
            for entry in globList:
                printEntry(entry, copy, follow='L')
        fileList, derefList = [], globList
    else:
        fileList, derefList = [], []
        for entry in globList:
            if _deref == 'H' and path.islink(entry) or entry.endswith(os.sep):
                if path.isfile(entry):
                    derefList.append(entry)
                elif path.isdir(entry):
                    contents = _glob(path.join(entry, '*'))
                    if contents:
                        fileList += contents
                    else:
                        derefList.append(entry)
                else:
                    fileList.append(entry)
                if _verbose:
                    printEntry(entry, copy,
                               follow=('H' if path.islink(entry.rstrip(os.sep))
                                           else False))
            else:
                if _verbose:
                    printEntry(entry, copy)
                fileList.append(entry)

    if copy:
        _tempdir.add(fileList, abspath)
        _tempdir.add(derefList, abspath, follow=True)
    else:
        _archive.add(fileList, abspath)
        _archive.add(derefList, abspath, follow=True)

    return True


def printEntry(entry, copy, follow=False):
    s = '_'
    print("[%c%c%c] " % ('D' if path.isdir(entry) else s,
                         'C' if copy else s,
                         follow if follow else s),
          (_tempdir.name + os.sep if copy else '') + entry)

def setTempdir(name):
    global _tempdir

    for td in _tempdirs:
        if td.name == name:
            _tempdir = td
            return
    _tempdir = Tempdir(name)
    _tempdirs.add(_tempdir)

def readLines(file, filedir):
    global _filedir

    saved_filedir = _filedir
    _filedir = filedir

    for line in file:
        try:
            processLine(line.strip())
        except OSError as e:
            PrintError(e.filename, e.strerror)
            updateStatus(1)
        finally:
            os.chdir(_rundir)

    _filedir = saved_filedir

def readFile(file, filedir, basename):
    global _archive

    format = strftime(path.expandvars(_format))
    format = format.replace(_format_token, basename).replace(os.sep, '_')

    ext = _re_archive_ext.search(format)
    _archive = Archive(_re_archive_ext.sub('', format),
                       ext.group() if ext else None)

    setTempdir(basename)

    readLines(file, filedir)

    if not _simulate:
        if all(td.commit() for td in _tempdirs):
            _archive.add([ td.name for td in _tempdirs if td.status is True ],
                         _dest)
            if _archive.commit() and _archive.compressAndReplace():
                TestPrint(_verbose, "done:", _archive.name)
                _archive = None
    else:
        if _verbose and (_archive.queues or any(td.queues for td in _tempdirs)):
            ProgPrint(_archive.name if _compress else _archive.final_name,
                      "will be created in", shortPath(_target))
            if _compress:
                ProgPrint("to be compressed using", _compress[0],
                          "to", _archive.final_name)

    cleanup()
    _tempdirs.clear()

def cleanup():
    if _archive is not None:
        _archive.remove()
    for td in _tempdirs:
        td.remove()

def extglob(pat):
    out = runProc( [ _extglob, pat ],
                   stdout=PIPE, stderr=PIPE, ignore_code=True )[0]
    return [ line for line in out.split('\n') if line ]


def updateStatus(code):
    global _status, _num_errors
    if code == 0:
        _status = 0
        _num_errors = 0
    else:
        _status = max(_status, code)
        _num_errors += 1

def instantiateGlobals():
    global _rundir, _filedir
    global _children
    global _tempdirs
    global _archive, _tempdir
    global _extglob
    global _cp_default
    global _tar_default
    global _tar_ext
    global _gzip, _bzip2
    global _compressed_exts
    _rundir, _filedir = os.getcwd(), None
    _children = set()
    _tempdirs = set()
    _archive, _tempdir = None, None
    _extglob = "extglob"
    _cp_default = [ 'cp', '-a', '--parents' ]
    _tar_default = [ 'tar' ]
    _tar_ext = '.tar'
    _gzip = 'gzip'
    _bzip2 = 'bzip2'
    _compressed_exts = {
        _gzip:  '.gz',
        _bzip2: '.bz2',
    }

    global _relative_pat
    global _format_token
    global _purge_chr
    global _copy_chr
    global _link_chr
    global _reserved_flags
    global _re_leading_flags
    global _re_comments
    global _re_tempdir
    global _re_quotes
    global _re_bare_quote
    global _re_escaped
    global _re_repeated_sep
    global _re_repeated_relative
    global _re_implied_part
    global _re_glob_part
    global _re_home
    global _re_archive_ext
    _relative_pat = os.sep + '.' + os.sep
    _format_token = '{}'
    _purge_chr, _copy_chr, _link_chr = '!', '%', '@'
    _reserved_flags = _purge_chr + _copy_chr + _link_chr

    s = os.sep.replace('\\', r'\\')
    relative_pat = s + r'\.' + s
    _re_leading_flags = re.compile(r'^(?P<flags>(?:[' + _reserved_flags + r']\s*)*)(?P<line>.*)')
    _re_comments = re.compile(r'^(?P<repl>(?:[^#"\\]|\\.|"(?:[^"\\]|\\.)*")*)'
                              r'(?P<comment>#.*)$')
    _re_tempdir = re.compile(_copy_chr + r'\s*(?P<tempdir>[\w\-+.]+)')
    _re_quotes = re.compile(r'(?P<repl1>(?:^|(?<=[^\\]))(?:\\\\)*)'
                            r'"(?P<repl2>(?:[^"\\]|\\.)*)"')
    _re_bare_quote = re.compile(r'(?:^|(?<=[^\\]))(?:\\\\)*"')
    _re_escaped = re.compile(r'\\(?P<repl>[#"\\])')
    _re_repeated_sep = re.compile(s + r'{2,}')
    _re_repeated_relative = re.compile(r'(?P<repl>' + relative_pat + r')'
                                       r'(?:\.' + s + r')+')
    _re_implied_part = re.compile(r'^.*?' + relative_pat + r'+(?P<repl>.*)$')
    _re_glob_part = re.compile(r'(?:^|' + s + r'|[^' + s + r']*[^' + s + r'\\])'
                               r'(?:\\\\)*(?:'
                               r'[*?]|'
                               r'\[[^!^' + s + r'][^' + s + r']*\]|'
                               r'\[[!^][^' + s + r']+\]|'
                               r'[*?+@!]\([^' + s + r']+\)'
                               r').*$')
    _re_home = re.compile(r'^' + path.expanduser('~'))
    _re_archive_ext = re.compile('\\' + _tar_ext + r'(?:\.(?:[zZ]|gz|bz2?))?$|'
                                 r'\.t(?:gz|bz2?)$')

def parseOptions(argv):
    global _target
    global _dest
    global _format
    global _deref
    global _force
    global _verbose
    global _simulate
    global _glob
    global _compress
    global _tar_create, _tar_append

    from optparse import OptionParser, OptParseError
    class OptParser(OptionParser):
        def error(self, msg):
            raise OptParseError(msg)
        def exit(self, status=0, msg=None):
            raise Exit(status, msg)
    try:
        parser = OptParser(prog=__prog__, version="%prog "+__version__,
                           usage=__usage__, add_help_option=False)
        parser.add_option("-h", "--help", default=False, action="store_true",
                          help='show this help message and exit')
        parser.add_option("-?", "--usage", default=False, action="store_true",
                          help='show a brief usage string and exit')
        parser.add_option("-t", "--target", metavar="DIRECTORY", default=_rundir,
                          help='create archives in DIRECTORY (default is the '
                               'current directory)')
        parser.add_option("-a", "--archive", metavar="FMT", default=_format_token,
                          help='format string for the archive name, on which '
                               'variable and strftime substitution will be '
                               'performed; the pattern "%default" will be '
                               'replaced with the name of the corresponding '
                               'input file, without the suffix')
        parser.add_option("-L", "--dereference", dest="dereference",
                          action="store_const", const="L",
                          help='follow all symbolic links')
        parser.add_option("-H", dest="dereference",
                          action="store_const", const="H",
                          help='try to follow any symbolic links specified by '
                               'a file pattern (and only those links)')
        parser.add_option("-f", "--force", default=False, action="store_true",
                          help='overwrite files without confirmation')
        parser.add_option("-v", "--verbose", default=False, action="store_true",
                          help='print messages')
        parser.add_option("-n", "--simulate", default=False, action="store_true",
                          help="read input files, but don't write to disk")
        parser.add_option("-e", "--extglob", default=False, action="store_true",
                          help="enable bash extended globbing (requires "
                               "`" + _extglob + "' in $PATH)")
        parser.add_option("-z", "--"+_gzip, dest="compress",
                          action="store_const", const=_gzip,
                          help='compress archives with ' + _gzip)
        parser.add_option("-j", "--"+_bzip2, dest="compress",
                          action="store_const", const=_bzip2,
                          help='compress archives with ' + _bzip2)
        opts, args = parser.parse_args(argv[1:])

        if opts.help:
            parser.print_version()
            print(__doc__ % globals())
            print()
            parser.print_help()
            raise Exit(0, None)
        elif opts.usage:
            parser.print_usage()
            raise Exit(0, None)

        try:
            _target = opts.target
            os.chdir(_target)
            os.chdir(_rundir)
            _dest = path.abspath(_target)
        except OSError as e:
            raise OptParseError(e.filename + ": " + e.strerror)

        _format = opts.archive
        if not _format:
            raise OptParseError("empty archive name")

        _deref = opts.dereference
        _force = opts.force
        _verbose = opts.verbose
        _simulate = opts.simulate

        if opts.extglob:
            _glob = extglob
        else:
            _glob = glob

        if opts.compress:
            _compress = [ opts.compress, '--stdout' ]
        else:
            _compress = None

        _cp_default.append('-t')
        _tar_default.append('-f')

        _tar_create = _tar_default[:1] + [ '--create' ] + _tar_default[1:]
        _tar_append = _tar_default[:1] + [ '--append' ] + _tar_default[1:]

        if len(args) == 0:
            raise OptParseError("no input file specified")

        return args

    except OptParseError as e:
        parser.print_usage(file=sys.stderr)
        raise Exit(2, e.msg)

def main(argv=None):
    if argv is None:
        argv = sys.argv

    signals = ("SIGINT", "SIGQUIT", "SIGABRT", "SIGHUP", "SIGTERM")
    saved_handlers = {}
    for signame in signals:
        signum = getattr(signal, signame, None)
        if signum:
            saved_handlers[signum] = signal.signal(signum, handler)
    try:
        global __prog__
        __prog__ = path.basename(argv[0])

        updateStatus(0)
        instantiateGlobals()

        args = parseOptions(argv)

        continued = False
        for arg in args:
            try:
                with open(arg) as file:
                    if _verbose:
                        TestPrint(continued)
                        ProgPrint("reading from", shortPath(arg))
                        ProgPrint("adding entries to queue")
                        ProgPrint("D [directory], C [copy], [LH] [dereference]")

                    readFile( file, path.dirname(arg),
                              path.splitext(path.basename(arg)) [0] )
                    continued = True
            except IOError as e:
                PrintError(e.filename, e.strerror)
                updateStatus(1)

        if _status != 0:
            TestPrint(_verbose and continued)
            TestPrint(_verbose, _num_errors, " error",
                      's' if _num_errors > 1 else '', sep='')
        return _status

    except Exit as e:
        PrintError(*e.args)
        return e.status

    finally:
        try:
            for proc in _children:
                proc.send_signal(signum)
            cleanup()
        except NameError:
            pass

        for signum in saved_handlers:
            signal.signal(signum, saved_handlers[signum])


if __name__ == '__main__':
    sys.exit(main())

