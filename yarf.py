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
#   2009-04-08  File created
#   2009-04-11  Documentation
#
#########################################################################

import sys, os, signal, re
from os import path
from glob import glob
from subprocess import Popen, PIPE

__version__ = "0.5"
__usage__ = ("Usage: %prog [-t DEST] [-s SRC] [-o RSYNC_OPTS]... "
                          "[-dzn] [-LHrve] [FILE]...")
__doc__ = """
Read file patterns from files or standard input and invoke `rsync' to transfer
files to a destination given by the "-t" option. Each line is read as a
globbing pattern for files or directories, or a descriptor for a remote source
to be transferred.

Tilde and variable expansion is performed on each pattern before pathname
expansion. The file may contain comments, which begin with a '#' and continue
to the end of the line. To include a '#' in the pattern, escape it with a
backslash or put it inside a double-quoted string. To include a '"' in the
pattern, escape it with a backslash. The pattern is otherwise literal
(including internal whitespace).

If a line starts with '%(_link_chr)c', lines from files matching the pattern are read as if
they were in the current input file.

Implied directories can be specified in the pattern to preserve directory
structure. This is done with the first "/./" marker in the file pattern, as in
newer versions of rsync (see the "--relative" rsync option). Also, any
directory with a globbing pattern and all its children are automatically
implied. Otherwise, only the path's basename is implied. So for example, the
first two patterns will preserve the parent directory containing "bar", while
the third will only transfer "bar" to the destination:

    ~/1/./foo/bar
    ~/2/*/bar
    ~/3/foo/bar

If a line starts with the "purge" character ('%(_purge_chr)c'), all files matching the
implied part of the pattern are deleted from the destination unless they exist
in SRC. For example, if we have

    %(_purge_chr)c ~/./.config/*.conf
    %(_purge_chr)c ~/foo/*/bar

and DEST is /bak, then files matching /bak/.config/*.conf and /bak/*/bar would
be purged. This operation is only attempted if DEST is local.

Implied directories are also used for changing the source directory
with the "-s" option. If SRC is set to /mnt, then /mnt/.config/*.conf and
/mnt/*/bar would be transferred instead.
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

def TestPrint(condition, *args, prog=True, sep=' ', end='\n', file=sys.stdout):
    if condition:
        if prog:
            ProgPrint(*args, sep=sep, end=end, file=file)
        else:
            print(*args, sep=sep, end=end, file=file)

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


def normPath(path):
    return _re_repeated_sep.sub(r'\g<repl>' + os.sep, path)

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
        line = normPath(line)
    return leading_flags, _re_repeated_relative.sub(r'\g<repl>', line)

def processLine(line):
    flags, pattern = parseLine(line)

    if not pattern:
        return False
    elif _copy_chr in flags:
        TestPrint(_verbose, "unrecognized flag:", line, file=sys.stderr)
        return False

    link = _link_chr in flags
    purge = _purge and _purge_chr in flags

    if link:
        try:
            link_path = path.join(_filedir, pattern)
            links = _glob(link_path)
            if not links:
                links.append(link_path)
            for link in links:
                with open(link) as file:
                    readFile(file, path.dirname(link))
            return True
        except IOError as e:
            PrintError(e.filename, e.strerror)
            updateStatus(1)
            return False

    relative = (_relative_pat in pattern)

    if not relative:
        implied_pat = path.basename(pattern)
        if not implied_pat:
            implied_pat = path.basename(path.dirname(pattern)) + os.sep
            implied_pat = implied_pat.lstrip(os.sep)
    else:
        implied_pat = _re_implied_part.sub(r'\g<repl>', pattern)

    glob_match = _re_glob_part.search(pattern)

    if glob_match:
        relative = True

        glob_pat = glob_match.group().lstrip(os.sep)
        if glob_pat == pattern:
            pattern = '.' + _relative_pat + pattern
        else:
            pos = len(glob_pat)
            if _relative_pat not in pattern[ : -pos]:
                pattern = normPath( pattern[ : -pos] + _relative_pat +
                                    pattern[-pos : ] )
    else:
        glob_pat = ''

    implied_pat = max(implied_pat, glob_pat, key=len)

    if _source:
        if implied_pat:
            pattern = path.join(_source, implied_pat)
        else:
            PrintError("empty implied part", pattern)
            updateStatus(1)
            return False

    fileList = _glob(pattern)

    if not fileList:
        if not _remote:
            PrintError("no matches", pattern)
            updateStatus(1)
            return False
        fileList.append(pattern)
        local = False
    else:
        local = True

    if purge and implied_pat:
        purgeMatching(implied_pat, _dest, pattern[ : -len(implied_pat)])

    if not local or not _deref:
        for entry in fileList:
            queueAdd(entry, relative)
    elif _deref == 'L':
        for entry in fileList:
            queueAdd(entry, relative, follow=True)
    elif _deref == 'H':
        for entry in fileList:
            relative_entry = relative
            follow = False

            if path.islink(entry):
                if path.isfile(entry):
                    follow = True
                elif path.isdir(entry):
                    if not relative:
                        pos = len(path.dirname(entry))
                        if pos == 0:
                            entry = '.' + _relative_pat + entry
                        else:
                            entry = normPath( entry[ : pos] + _relative_pat +
                                              entry[pos : ] )
                        relative_entry = True
                    entry += os.sep

            queueAdd(entry, relative_entry, follow)

    return True


def purgeMatching(pat, dest, src):
    dest_pat = path.join(dest, pat)
    destList = _glob(dest_pat)
    if not destList:
        return
    TestPrint(_verbose)
    TestPrint(_verbose, "purging from destination:" if not _simulate else
                        "to be purged:", shortPath(dest_pat))
    multi = len(destList) > 1
    if not _simulate:
        for entry in destList:
            if path.exists(path.join(src, entry[len(dest) : ].lstrip(os.sep))):
                continue
            try:
                walkRemove(entry)
                TestPrint(_verbose and multi, shortPath(entry), prog=False)
            except OSError as e:
                PrintError("error removing destination file",
                           e.filename, e.strerror)
                updateStatus(1)
    TestPrint(_verbose)

def queueAdd(entry, relative, follow=False):
    s = '_'
    TestPrint(_verbose, "[%c%c] " % ('R' if relative else s,
                                     'L' if follow else s),
                        shortPath(entry), prog=False)
    _queues[(relative, follow)].append(entry)


def readFile(file, filedir):
    global _filedir

    saved_filedir = _filedir
    _filedir = filedir

    for line in file:
        processLine(line.strip())

    _filedir = saved_filedir

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

def rsyncList(srcList, relative, follow):
    if not srcList:
        return

    options = []
    if relative:
        options.append('--relative')
    if follow:
        options.append('--copy-links')

    runProc(_rsync_default + options + srcList + [ _dest ])

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
    global _extglob
    global _queues
    global _rsync_default
    _rundir, _filedir = os.getcwd(), None
    _children = set()
    _extglob = "extglob"
    _queues = {}
    for relative in (True, False):
        for follow in (True, False):
            _queues[(relative, follow)] = []
    _rsync_default = [ 'rsync', '-a' ]

    global _relative_pat
    global _purge_chr
    global _copy_chr
    global _link_chr
    global _reserved_flags
    global _re_leading_flags
    global _re_comments
    global _re_quotes
    global _re_bare_quote
    global _re_escaped
    global _re_repeated_sep
    global _re_repeated_relative
    global _re_implied_part
    global _re_glob_part
    global _re_home
    _relative_pat = os.sep + '.' + os.sep
    _purge_chr, _copy_chr, _link_chr = '!', '%', '@'
    _reserved_flags = _purge_chr + _copy_chr + _link_chr

    s = os.sep.replace('\\', r'\\')
    relative_pat = s + r'\.' + s
    _re_leading_flags = re.compile(r'^(?P<flags>(?:[' + _reserved_flags + r']\s*)*)(?P<line>.*)')
    _re_comments = re.compile(r'^(?P<repl>(?:[^#"\\]|\\.|"(?:[^"\\]|\\.)*")*)#.*$')
    _re_quotes = re.compile(r'(?P<repl1>(?:^|(?<=[^\\]))(?:\\\\)*)'
                            r'"(?P<repl2>(?:[^"\\]|\\.)*)"')
    _re_bare_quote = re.compile(r'(?:^|(?<=[^\\]))(?:\\\\)*"')
    _re_escaped = re.compile(r'\\(?P<repl>[#"\\])')
    _re_repeated_sep = re.compile(r'(?P<repl>^|[^:])' + s + r'{2,}')
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

def parseOptions(argv):
    global _rsync_default
    global _dest
    global _source
    global _simulate
    global _deref
    global _verbose
    global _purge
    global _remote
    global _glob

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
        parser.add_option("-?", "--usage", default=0, action="count",
                          help='show a brief usage string and exit')
        parser.add_option("-t", "--target", metavar="DEST", default=_rundir,
                          help='set the rsync destination to DEST (default is '
                               'the current directory)')
        parser.add_option("-s", "--source", metavar="SRC",
                          help='prepend SRC to the implied part of each file '
                               'pattern and use that instead (i.e. transfer '
                               'files rooted in SRC)')
        parser.add_option("-o", "--options", metavar="RSYNC_OPTS",
                          default=[], action="append",
                          help='options to pass to rsync in addition to the '
                               'defaults ("' + ' '.join(_rsync_default[1:]) +
                               '"); for example: --options="-cu --exclude=.git"')
        parser.add_option("-d", "--delete", default=False, action="store_true",
                          help='pass the "--delete" option to rsync')
        parser.add_option("-z", "--compress", default=False, action="store_true",
                          help='pass the "--compress" option to rsync')
        parser.add_option("-n", "--simulate", default=False, action="store_true",
                          help='if not --verbose, pass the "--dry-run" option '
                               'to rsync and print the file list; otherwise, '
                               'skip running rsync and only print output from ' +
                               __prog__ + ' (in both cases, no files would be '
                               'purged from the destination)')
        parser.add_option("-L", "--dereference", dest="dereference",
                          action="store_const", const="L",
                          help='follow all symbolic links')
        parser.add_option("-H", dest="dereference",
                          action="store_const", const="H",
                          help='try to follow any symbolic links specified by '
                               'a file pattern (and only those links)')
        parser.add_option("-r", "--remote", default=False, action="store_true",
                          help='allow patterns to describe remote sources')
        parser.add_option("-v", "--verbose", default=False, action="store_true",
                          help='print messages from ' + __prog__ + ' (use '
                               '--options for rsync verbosity)')
        parser.add_option("-e", "--extglob", default=False, action="store_true",
                          help="enable bash extended globbing (requires "
                               "`" + _extglob + "' in $PATH)")
        opts, args = parser.parse_args(argv[1:])

        if opts.help:
            parser.print_version()
            print(__doc__ % globals())
            print()
            parser.print_help()
            raise Exit(0, None)
        elif opts.usage > 0:
            parser.print_usage()
            foo = ("There is no spoon.",
                   "There is no snooze button on a cat who wants breakfast.",
                   "There is no pleasure in having nothing to do; "+
                   "the fun is in having lots to do\nand not doing it.",
                   "There is no substitute for butter.")
            if opts.usage > 12:
                print("No lack of boredom in you there is.")
            elif opts.usage > 2:
                import random
                print(random.choice(foo))
            raise Exit(0, None)

        _dest = opts.target
        _source = opts.source

        options = []
        for opt_str in opts.options:
            if opt_str.startswith('-'):
                options += opt_str.split()
            else:
                raise OptParseError("invalid rsync option: " + opt_str)
        _rsync_default += options

        if opts.delete:
            _rsync_default.append('--delete')

        if opts.compress:
            _rsync_default.append('--compress')

        _simulate = opts.simulate
        _deref = opts.dereference
        _verbose = opts.verbose
        _purge = path.exists(_dest) and (_verbose or not _simulate)
        _remote = opts.remote

        if _simulate and not _verbose:
            _rsync_default += [ '--dry-run', '--out-format=%n%L' ]

        if opts.extglob:
            _glob = extglob
        else:
            _glob = glob

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

        TestPrint(_verbose, "adding entries to queue")
        TestPrint(_verbose, "[RL]  entry-specific rsync options:",
                            "R [--relative], L [--copy-links]", prog=False)
        TestPrint(_verbose)
        if len(args) == 0:
            TestPrint(_verbose, "reading from stdin")
            readFile(sys.stdin, _rundir)
            TestPrint(_verbose)
        else:
            for arg in args:
                try:
                    with open(arg) as file:
                        TestPrint(_verbose, "reading from", shortPath(arg))
                        readFile(file, path.dirname(arg))
                        TestPrint(_verbose)
                except IOError as e:
                    PrintError(e.filename, e.strerror)
                    updateStatus(1)

        if _verbose and any(_queues.values()):
            ProgPrint("destination is set to", shortPath(_dest))
            ProgPrint('invoking rsync with "' if not _simulate else
                      'rsync would be invoked with "',
                      ' '.join(_rsync_default[1:]), '"', sep='')
        if not (_verbose and _simulate):
            for flags in _queues:
                rsyncList(_queues[flags], flags[0], flags[1])

        if _status == 0:
            TestPrint(_verbose and not _simulate, "done")
        else:
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
        except NameError:
            pass

        for signum in saved_handlers:
            signal.signal(signum, saved_handlers[signum])


if __name__ == '__main__':
    sys.exit(main())

