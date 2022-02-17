import datetime
import re
import pathlib
import os
import tempfile


class TodoTxt:
    """Access to a todo.txt file

    Common use::

        todotxt = TodoTxt("todo.txt")
        todotxt.parse()

    Use the ``tasks`` property to access the parsed entries.
    """
    def __init__(self, filename, encoding='utf-8', task_class=None):
        self.filename = pathlib.Path(filename)
        self.encoding = encoding
        self.linesep = os.linesep
        self.task_class = Task if task_class is None else task_class
        self.tasks = []

    def parse(self):
        """(Re)parse the todo.txt file"""
        self.tasks = []

        # process task lines of file
        with open(self.filename, 'rt', encoding=self.encoding) as fd:
            lines = fd.readlines()

            # remember newline separator
            if isinstance(fd.newlines, str):
                self.linesep = fd.newlines
            # handle the case when multiple newline separators are detected
            elif isinstance(fd.newlines, tuple):
                # when the OS newline seperator has been used in the source file
                if os.linesep in fd.newlines:
                    # use the system default newline separator
                    self.linesep = os.linesep
                # otherwise if the newline seperator of the OS has not been used in the source file but other ones
                else:
                    # use the first found newline separator
                    self.linesep = fd.newlines[0]

            self.parse_from_lines(lines)

        return self.tasks

    def parse_from_lines(self, lines, filter_func=None):
        """(Re)parse an input from list of line of text
        used by parse()
        filter_func: an optional function or method to get str from
                     if lines contain more complex object.
        """
        self.tasks = []

        # read lines and parse them as tasks
        for linenr, line in enumerate(lines):
            if callable(filter_func):
                line = filter_func(line)

            if len(line.strip()) == 0:
                continue
            self.add_task(line, linenr)

        return self.tasks

    def add_task(self, line, linenr=None):
        if linenr is None:
            linenr = len(self.tasks)
        task = self.task_class(line.strip(), linenr=linenr, todotxt=self)
        self.tasks.append(task)

    def save(self, target=None, safe=True, linesep=None):
        """Save all tasks to disk

        If ``target`` is not provided, the ``filename`` property is being
        used as the target file to save to.

        If ``safe`` is set (the default), the file will first be written to
        a temporary file in the same folder as the target file and after the
        successful write to disk, it will be moved in place of ``target``.
        This can cause trouble though with folders that are synchronised to
        some cloud storage.

        With ``linesep`` you can specify the line seperator. If it is not set
        it defaults to the systems default line seperator.
        """
        if target is None:
            target = self.filename
        else:
            target = pathlib.Path(target)
        write_to = target

        tmpfile = None
        if safe:
            tmpfile = tempfile.NamedTemporaryFile(dir=self.filename.parent,
                                                  delete=False,
                                                  prefix=".tmp",
                                                  suffix="~")
            write_to = tmpfile.name
            tmpfile.close()

        if linesep is None:
            linesep = self.linesep

        with open(write_to, 'wb', buffering=0) as fd:
            lines = [ l + linesep for l in self.get_text_lines() ]
            fd.write(bytes(''.join(lines), self.encoding))

        if safe:
            os.replace(write_to, target)

            try:
                os.unlink(write_to)
            except OSError:
                pass

    def get_text_lines(self):
        """Get all Task as list on lines (str)
        """
        return [str(task) for task in
                 sorted(self.tasks, key=lambda t: t.linenr if t.linenr is not None else len(self.tasks))]

    def __repr__(self):
        return f'{self.__class__.__name__}(filename="{self.filename}")'


class Task:
    """A task of a todo.txt file

    The usual way to create a task is to create it from an initial string::

        task = Task("(B) some task")

    or::

        task = Task()
        task.parse("(B) some task")

    The inverse operation to parsing is to convert the task to a string::

        task = Task("(B) some task")
        assert str(task) == "(B) some task"

    """
    COMPLETED_RE = re.compile(r'^x\s+')
    PRIORITY_RE = re.compile(r'^\s*\(([A-Z]+)\)')
    PROJECT_RE = re.compile(r'(\s+|^)\+([^\s]+)')
    CONTEXT_RE = re.compile(r'(\s+|^)@([^\s]+)')
    KEYVALUE_RE = re.compile(r'(\s+|^)([^\s]+):([^\s$]+)')
    DATE_RE = re.compile(r'^\s*([\d]{4}-[\d]{2}-[\d]{2})', re.ASCII)
    DATE_FMT = '%Y-%m-%d'
    KEYVALUE_ALLOW = set(['http', 'https', 'mailto', 'ssh', 'ftp'])

    def __init__(self, line=None, linenr=None, todotxt=None):
        """Create a new task

        ``line`` is the raw string representation (one line of a todo.txt file).
        ``linenr`` is the line number within the ``todotxt`` file, if any.
        """
        self.description = None
        self.is_completed = None
        self.priority = None
        self.completion_date = None
        self.creation_date = None
        self.linenr = linenr
        self.todotxt = todotxt
        self._raw = None
        self._attributes = None

        if line is not None and len(line.strip()) > 0:
            self.parse(line)

    def remove_project(self, project):
        return self.remove_tag(project, self.__class__.PROJECT_RE)

    def remove_context(self, context):
        return self.remove_tag(context, self.__class__.CONTEXT_RE)

    def remove_attribute(self, key, value=None):
        """Remove attribute `key` from the task
        If you provide a `value` only the attribute with that key and value is removed.
        If no `value` is provided all attributes with that key are removed.
        """
        success = False
        while True:
            key_found = False

            for match in self.parse_tags(self.__class__.KEYVALUE_RE):
                if key != match.group(2):
                    continue

                key_found = True

                if value is None or match.group(3) == value:
                    start, end = match.span()
                    self.description = self.description[:start]
                    self.parse(str(self))
                    success = True

                break

            if not key_found:
                break

        return success

    def remove_tag(self, text, regex):
        for match in self.parse_tags(regex):
            if match.group(2) == text:
                start, end = match.span()
                self.description = self.description[:start] + self.description[end:]
                self.parse(str(self))
                return True
        return False

    def replace_attribute(self, key, value, newvalue):
        """Replace the value of key:value in place with key:newvalue"""
        for match in self.parse_tags(self.__class__.KEYVALUE_RE):
            if key != match.group(2) or value != match.group(3):
                continue

            self.description = self.description[:match.start(3)] + \
                               newvalue + \
                               self.description[match.end(3):]
            self.parse(str(self))
            return True

        return False

    def replace_context(self, context, newcontext):
        """Replace the first occurrence of @context with @newcontext"""
        return self.replace_tag(context, newcontext, self.__class__.CONTEXT_RE)

    def replace_project(self, project, newproject):
        """Replace the first occurrence of @project with @newproject"""
        return self.replace_tag(project, newproject, self.__class__.PROJECT_RE)

    def replace_tag(self, value, newvalue, regex):
        for match in self.parse_tags(regex):
            if match.group(2) == value:
                self.description = self.description[:match.start(2)] + \
                                   newvalue + \
                                   self.description[match.end(2):]
                self.parse(str(self))
                return True
        return False

    def add_project(self, project):
        self.append('+' + project)
        self.parse(str(self))

    def add_context(self, context):
        self.append('@' + context)
        self.parse(str(self))

    def add_attribute(self, key, value):
        self.append(f'{key}:{value}')
        self.parse(str(self))

    def append(self, text, add_space=True):
        if self.description is None:
            self.description = text
        else:
            if add_space and not self.description.endswith(' '):
                self.description += ' '
            self.description += text

    def bare_description(self):
        """The description of the task without contexts, projects or any other attributes"""
        if self.description is None:
            return ''

        parts = []
        for part in self.description.split(' '):
            if len(part) == 0 or part[0] in '@+':
                continue
            # make sure attributes with keys in KEYVALUE_ALLOW are included in bare description
            elif ':' in part:
                attrribute_key = part[:part.index(":")]
                if attrribute_key.lower() in self.__class__.KEYVALUE_ALLOW:
                    parts.append(part)
            else:
                parts.append(part)

        return ' '.join(parts)

    @property
    def projects(self):
        return [match.group(2) for match in self.parse_tags(self.__class__.PROJECT_RE)]

    @property
    def contexts(self):
        return [match.group(2) for match in self.parse_tags(self.__class__.CONTEXT_RE)]

    @property
    def attributes(self):
        if self._attributes is None:
            self.parse_attributes()
        return self._attributes

    def __getattr__(self, name, fallback=None):
        if name.startswith('attr_'):
            if fallback is None:
                fallback = []
            _, attrname = name.split('_', 1)
            return self.attributes.get(attrname, fallback)
        raise AttributeError(name)

    def parse_attributes(self):
        self._attributes = {}
        for match in self.parse_tags(self.__class__.KEYVALUE_RE):
            key = match.group(2)
            value = match.group(3)
            if key.lower() in self.__class__.KEYVALUE_ALLOW:
                continue
            if key not in self._attributes:
                self._attributes[key] = []
            self._attributes[key].append(value)

    def parse_tags(self, regex):
        matches = []
        if self.description is None:
            return matches

        for match in regex.finditer(self.description):
            if match:
                matches.append(match)
            else:
                break
        return matches

    def parse_priority(self, line):
        self.priority = None
        match = self.__class__.PRIORITY_RE.match(line)
        if match:
            self.priority = match.group(1)
            line = line[match.span()[1]:]
        return line

    @classmethod
    def match_date(cls, line):
        match = cls.DATE_RE.match(line)
        date = None
        if match:
            date = cls.parse_date(match.group(1))
            line = line[match.span()[1]:]
        return line, date

    @classmethod
    def parse_date(cls, text):
        return datetime.datetime.strptime(text, cls.DATE_FMT).date()

    def parse(self, line):
        """(Re)parse the task

        ``line`` is the raw string representation of a task, i.e. one line
        of a todo.txt file.
        """
        self._raw = line
        line = line.strip()

        # completed or not
        match = self.__class__.COMPLETED_RE.match(line)
        self.is_completed = match is not None
        if match:
            # strip the leading mark
            line = line[match.span()[1]:]

        if self.is_completed:
            line, self.completion_date = self.__class__.match_date(line)

        line = self.parse_priority(line)
        line, self.creation_date = self.__class__.match_date(line)

        # description
        if len(line) > 0:
            self.description = line.strip()

        self.parse_attributes()

    def __str__(self):
        """The todo.txt compatible representation of this task."""
        result = ''
        if self.is_completed:
            result += 'x '

            if self.completion_date is not None:
                result += self.completion_date.strftime(self.__class__.DATE_FMT) + ' '

        if self.priority:
            if not self.is_completed:
                result += f'({self.priority}) '

        if self.creation_date is not None:
            result += self.creation_date.strftime(self.__class__.DATE_FMT) + ' '

        if self.description:
            result += self.description

        return result

    def set_completed(self, completed=True, completion_date=None):
        self.is_completed = completed

        if completion_date is not None:
            self.completion_date = completion_date

        # update parent item in tasks[]
        if self.todotxt and self.linenr and self == self.todotxt.tasks[self.linenr]:
            self.todotxt.tasks[self.linenr].is_completed = self.is_completed
            self.todotxt.tasks[self.linenr].completion_date = self.completion_date

    def __repr__(self):
        return f'{self.__class__.__name__}({repr(str(self))})'


# for backwards compatability
match_date = Task.match_date
parse_date = Task.parse_date
