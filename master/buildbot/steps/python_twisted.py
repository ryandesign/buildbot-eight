# This file is part of Buildbot.  Buildbot is free software: you can
# redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation, version 2.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Copyright Buildbot Team Members
"""
BuildSteps that are specific to the Twisted source tree
"""


import re

from twisted.internet import defer
from twisted.python import log

from buildbot.process import buildstep
from buildbot.process import logobserver
from buildbot.process.results import FAILURE
from buildbot.process.results import SKIPPED
from buildbot.process.results import SUCCESS
from buildbot.process.results import WARNINGS
from buildbot.steps.shell import ShellCommand


class HLint(buildstep.ShellMixin, buildstep.BuildStep):

    """I run a 'lint' checker over a set of .xhtml files. Any deviations
    from recommended style is flagged and put in the output log.

    This step looks at .changes in the parent Build to extract a list of
    Lore XHTML files to check."""

    name = "hlint"
    description = "running hlint"
    descriptionDone = "hlint"
    warnOnWarnings = True
    warnOnFailure = True
    # TODO: track time, but not output
    warnings = 0

    def __init__(self, python=None, **kwargs):
        kwargs = self.setupShellMixin(kwargs, prohibitArgs=['command'])
        super().__init__(**kwargs)
        self.python = python
        self.warningLines = []
        self.addLogObserver(
            'stdio', logobserver.LineConsumerLogObserver(self.logConsumer))

    @defer.inlineCallbacks
    def run(self):
        # create the command
        htmlFiles = {}
        for f in self.build.allFiles():
            if f.endswith(".xhtml") and not f.startswith("sandbox/"):
                htmlFiles[f] = 1
        # remove duplicates
        hlintTargets = sorted(htmlFiles.keys())
        if not hlintTargets:
            return SKIPPED
        self.hlintFiles = hlintTargets

        command = []
        if self.python:
            command.append(self.python)
        command += ["bin/lore", "-p", "--output", "lint"] + self.hlintFiles

        cmd = yield self.makeRemoteShellCommand(command=command)
        yield self.runCommand(cmd)

        stdio_log = yield self.getLog('stdio')
        yield stdio_log.finish()

        yield self.addCompleteLog('warnings', '\n'.join(self.warningLines))
        yield self.addCompleteLog("files", "\n".join(self.hlintFiles) + "\n")

        # warnings are in stdout, rc is always 0, unless the tools break
        if cmd.didFail():
            return FAILURE

        self.descriptionDone = "{} hlin{}".format(self.warnings, self.warnings == 1 and 't' or 'ts')

        if self.warnings:
            return WARNINGS
        return SUCCESS

    def logConsumer(self):
        while True:
            stream, line = yield
            if ':' in line:
                self.warnings += 1
                self.warningLines.append(line)


class TrialTestCaseCounter(logobserver.LogLineObserver):
    _line_re = re.compile(r'^(?:Doctest: )?([\w\.]+) \.\.\. \[([^\]]+)\]$')

    def __init__(self):
        super().__init__()
        self.numTests = 0
        self.finished = False
        self.counts = {'total': None,
                       'failures': 0,
                       'errors': 0,
                       'skips': 0,
                       'expectedFailures': 0,
                       'unexpectedSuccesses': 0,
                       }

    def outLineReceived(self, line):
        # different versions of Twisted emit different per-test lines with
        # the bwverbose reporter.
        #  2.0.0: testSlave (buildbot.test.test_runner.Create) ... [OK]
        #  2.1.0: buildbot.test.test_runner.Create.testSlave ... [OK]
        #  2.4.0: buildbot.test.test_runner.Create.testSlave ... [OK]
        # Let's just handle the most recent version, since it's the easiest.
        # Note that doctests create lines line this:
        #  Doctest: viff.field.GF ... [OK]

        if line.startswith("=" * 40):
            self.finished = True
        if not self.finished:
            m = self._line_re.search(line.strip())
            if m:
                testname, result = m.groups()
                self.numTests += 1
                self.step.setProgress('tests', self.numTests)

        out = re.search(r'Ran (\d+) tests', line)
        if out:
            self.counts['total'] = int(out.group(1))
        if (line.startswith("OK") or
            line.startswith("FAILED ") or
                line.startswith("PASSED")):
            # the extra space on FAILED_ is to distinguish the overall
            # status from an individual test which failed. The lack of a
            # space on the OK is because it may be printed without any
            # additional text (if there are no skips,etc)
            out = re.search(r'failures=(\d+)', line)
            if out:
                self.counts['failures'] = int(out.group(1))
            out = re.search(r'errors=(\d+)', line)
            if out:
                self.counts['errors'] = int(out.group(1))
            out = re.search(r'skips=(\d+)', line)
            if out:
                self.counts['skips'] = int(out.group(1))
            out = re.search(r'expectedFailures=(\d+)', line)
            if out:
                self.counts['expectedFailures'] = int(out.group(1))
            out = re.search(r'unexpectedSuccesses=(\d+)', line)
            if out:
                self.counts['unexpectedSuccesses'] = int(out.group(1))
            # successes= is a Twisted-2.0 addition, and is not currently used
            out = re.search(r'successes=(\d+)', line)
            if out:
                self.counts['successes'] = int(out.group(1))


UNSPECIFIED = ()  # since None is a valid choice


class Trial(ShellCommand):

    """
    There are some class attributes which may be usefully overridden
    by subclasses. 'trialMode' and 'trialArgs' can influence the trial
    command line.
    """

    name = "trial"
    progressMetrics = ('output', 'tests', 'test.log')
    # note: the slash only works on unix workers, of course, but we have
    # no way to know what the worker uses as a separator.
    # TODO: figure out something clever.
    logfiles = {"test.log": "_trial_temp/test.log"}
    # we use test.log to track Progress at the end of __init__()

    renderables = ['tests', 'jobs']
    flunkOnFailure = True
    python = None
    trial = "trial"
    trialMode = ["--reporter=bwverbose"]  # requires Twisted-2.1.0 or newer
    # for Twisted-2.0.0 or 1.3.0, use ["-o"] instead
    trialArgs = []
    jobs = None
    testpath = UNSPECIFIED  # required (but can be None)
    testChanges = False  # TODO: needs better name
    recurse = False
    reactor = None
    randomly = False
    tests = None  # required

    def __init__(self, reactor=UNSPECIFIED, python=None, trial=None,
                 testpath=UNSPECIFIED,
                 tests=None, testChanges=None,
                 recurse=None, randomly=None,
                 trialMode=None, trialArgs=None, jobs=None,
                 **kwargs):
        super().__init__(**kwargs)

        if python:
            self.python = python
        if self.python is not None:
            if isinstance(self.python, str):
                self.python = [self.python]
            for s in self.python:
                if " " in s:
                    # this is not strictly an error, but I suspect more
                    # people will accidentally try to use python="python2.3
                    # -Wall" than will use embedded spaces in a python flag
                    log.msg("python= component '%s' has spaces")
                    log.msg("To add -Wall, use python=['python', '-Wall']")
                    why = "python= value has spaces, probably an error"
                    raise ValueError(why)

        if trial:
            self.trial = trial
        if " " in self.trial:
            raise ValueError("trial= value has spaces")
        if trialMode is not None:
            self.trialMode = trialMode
        if trialArgs is not None:
            self.trialArgs = trialArgs
        if jobs is not None:
            self.jobs = jobs

        if testpath is not UNSPECIFIED:
            self.testpath = testpath
        if self.testpath is UNSPECIFIED:
            raise ValueError("You must specify testpath= (it can be None)")
        assert isinstance(self.testpath, str) or self.testpath is None

        if reactor is not UNSPECIFIED:
            self.reactor = reactor

        if tests is not None:
            self.tests = tests
        if isinstance(self.tests, str):
            self.tests = [self.tests]
        if testChanges is not None:
            self.testChanges = testChanges
            # self.recurse = True  # not sure this is necessary

        if not self.testChanges and self.tests is None:
            raise ValueError("Must either set testChanges= or provide tests=")

        if recurse is not None:
            self.recurse = recurse
        if randomly is not None:
            self.randomly = randomly

        # build up most of the command, then stash it until start()
        command = []
        if self.python:
            command.extend(self.python)
        command.append(self.trial)
        command.extend(self.trialMode)
        if self.recurse:
            command.append("--recurse")
        if self.reactor:
            command.append("--reactor={}".format(reactor))
        if self.randomly:
            command.append("--random=0")
        command.extend(self.trialArgs)
        self.command = command

        if self.reactor:
            self.description = ["testing", "({})".format(self.reactor)]
            self.descriptionDone = ["tests"]
            # commandComplete adds (reactorname) to self.text
        else:
            self.description = ["testing"]
            self.descriptionDone = ["tests"]

        # this counter will feed Progress along the 'test cases' metric
        self.observer = TrialTestCaseCounter()
        self.addLogObserver('stdio', self.observer)

        # this observer consumes multiple lines in a go, so it can't be easily
        # handled in TrialTestCaseCounter.
        self.addLogObserver(
            'stdio', logobserver.LineConsumerLogObserver(self.logConsumer))
        self.problems = []
        self.warnings = {}

        # text used before commandComplete runs
        self.text = 'running'

    def setupEnvironment(self, cmd):
        super().setupEnvironment(cmd)
        if self.testpath is not None:
            e = cmd.args['env']
            if e is None:
                cmd.args['env'] = {'PYTHONPATH': self.testpath}
            else:
                # this bit produces a list, which can be used
                # by buildbot_worker.runprocess.RunProcess
                ppath = e.get('PYTHONPATH', self.testpath)
                if isinstance(ppath, str):
                    ppath = [ppath]
                if self.testpath not in ppath:
                    ppath.insert(0, self.testpath)
                e['PYTHONPATH'] = ppath

    def start(self):
        # choose progressMetrics and logfiles based on whether trial is being
        # run with multiple workers or not.
        output_observer = logobserver.OutputProgressObserver('test.log')

        if self.jobs is not None:
            self.jobs = int(self.jobs)
            self.command.append("--jobs=%d" % self.jobs)

            # using -j/--jobs flag produces more than one test log.
            self.logfiles = {}
            for i in range(self.jobs):
                self.logfiles['test.%d.log' %
                              i] = '_trial_temp/%d/test.log' % i
                self.logfiles['err.%d.log' % i] = '_trial_temp/%d/err.log' % i
                self.logfiles['out.%d.log' % i] = '_trial_temp/%d/out.log' % i
                self.addLogObserver('test.%d.log' % i, output_observer)
        else:
            # this one just measures bytes of output in _trial_temp/test.log
            self.addLogObserver('test.log', output_observer)

        # now that self.build.allFiles() is nailed down, finish building the
        # command
        if self.testChanges:
            for f in self.build.allFiles():
                if f.endswith(".py"):
                    self.command.append("--testmodule={}".format(f))
        else:
            self.command.extend(self.tests)
        log.msg("Trial.start: command is", self.command)

        super().start()

    def commandComplete(self, cmd):
        # figure out all status, then let the various hook functions return
        # different pieces of it

        counts = self.observer.counts

        total = counts['total']
        failures, errors = counts['failures'], counts['errors']
        parsed = (total is not None)
        text = []
        text2 = ""

        if not cmd.didFail():
            if parsed:
                results = SUCCESS
                if total:
                    text += ["{} {}".format(total, total == 1 and "test" or "tests"), "passed"]
                else:
                    text += ["no tests", "run"]
            else:
                results = FAILURE
                text += ["testlog", "unparseable"]
                text2 = "tests"
        else:
            # something failed
            results = FAILURE
            if parsed:
                text.append("tests")
                if failures:
                    text.append("{} {}".format(failures, failures == 1 and "failure" or "failures"))
                if errors:
                    text.append("{} {}".format(errors, errors == 1 and "error" or "errors"))
                count = failures + errors
                text2 = "{} tes{}".format(count, (count == 1 and 't' or 'ts'))
            else:
                text += ["tests", "failed"]
                text2 = "tests"

        if counts['skips']:
            text.append("{} {}".format(counts['skips'], counts['skips'] == 1 and "skip" or "skips"))
        if counts['expectedFailures']:
            text.append("{} {}".format(counts['expectedFailures'],
                                       counts['expectedFailures'] == 1 and "todo" or "todos"))
            if 0:  # TODO  pylint: disable=using-constant-test
                results = WARNINGS
                if not text2:
                    text2 = "todo"

        if 0:  # pylint: disable=using-constant-test
            # ignore unexpectedSuccesses for now, but it should really mark
            # the build WARNING
            if counts['unexpectedSuccesses']:
                text.append("{} surprises".format(counts['unexpectedSuccesses']))
                results = WARNINGS
                if not text2:
                    text2 = "tests"

        if self.reactor:
            text.append(self.rtext('(%s)'))
            if text2:
                text2 = "{} {}".format(text2, self.rtext('(%s)'))

        self.results = results
        self.text = text
        self.text2 = [text2]

    def rtext(self, fmt='{}'):
        if self.reactor:
            rtext = fmt.format(self.reactor)
            return rtext.replace("reactor", "")
        return ""

    def logConsumer(self):
        while True:
            stream, line = yield
            if line.find(" exceptions.DeprecationWarning: ") != -1:
                # no source
                warning = line  # TODO: consider stripping basedir prefix here
                self.warnings[warning] = self.warnings.get(warning, 0) + 1
            elif (line.find(" DeprecationWarning: ") != -1 or
                  line.find(" UserWarning: ") != -1):
                # next line is the source
                warning = line + "\n" + (yield)[1] + "\n"
                self.warnings[warning] = self.warnings.get(warning, 0) + 1
            elif line.find("Warning: ") != -1:
                warning = line
                self.warnings[warning] = self.warnings.get(warning, 0) + 1

            if line.find("=" * 60) == 0 or line.find("-" * 60) == 0:
                # read to EOF
                while True:
                    self.problems.append(line)
                    stream, line = yield

    def createSummary(self, loog):
        problems = '\n'.join(self.problems)
        warnings = self.warnings

        if problems:
            self.addCompleteLog("problems", problems)

        if warnings:
            lines = sorted(warnings.keys())
            self.addCompleteLog("warnings", "".join(lines))

    def evaluateCommand(self, cmd):
        return self.results

    def describe(self, done=False):
        return self.text


class RemovePYCs(ShellCommand):
    name = "remove-.pyc"
    command = ['find', '.', '-name', "'*.pyc'", '-exec', 'rm', '{}', ';']
    description = ["removing", ".pyc", "files"]
    descriptionDone = ["remove", ".pycs"]
