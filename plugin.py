###
# Copyright (c) 2007, Max Kanat-Alexander
# Copyright (c) 2011, Jack Grigg
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#   * Redistributions of source code must retain the above copyright notice,
#     this list of conditions, and the following disclaimer.
#   * Redistributions in binary form must reproduce the above copyright notice,
#     this list of conditions, and the following disclaimer in the
#     documentation and/or other materials provided with the distribution.
#   * Neither the name of the author of this software nor the name of
#     contributors to this software may be used to endorse or promote products
#     derived from this software without specific prior written consent.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

###


import supybot.utils as utils
from supybot.utils.structures import TimeoutQueue
from supybot.commands import *
import supybot.conf as conf
import supybot.world as world
import supybot.plugins as plugins
import supybot.ircmsgs as ircmsgs
import supybot.ircutils as ircutils
import supybot.registry as registry
import supybot.schedule as schedule
import supybot.callbacks as callbacks
import supybot.plugins.Web.plugin as Web

import re
import urllib
import urllib2
import csv

import bugmail
import traceparser

import mailbox
import email
from time import time, sleep
import os
import errno
import sys
try:
    import fcntl
except ImportError:
    fcntl = None

'''The maximum amount of time that the bugmail poller will wait
   for a dotlock to be released, in seconds, before throwing an
   exception.'''
MAX_DOTLOCK_WAIT = 300

'''For attachment.cgi in edit mode, how many bytes, starting at the
   beginning of the page, should we search through to get the title?'''
ATTACH_TITLE_SIZE = 512

######################################
# Utility Functions for Mbox Polling #
###################################### 

def _lock_file(f):
    """Lock file f using lockf and dot locking."""
    # XXX This seems to be causing problems in directories that we don't own.
    return
    dotlock_done = False
    try:
        if fcntl:
            fcntl.lockf(f, fcntl.LOCK_EX)

        pre_lock = _create_temporary(f.name + '.lock')
        pre_lock.close()

        start_dotlock = time()
        while (not dotlock_done):
            try:
                if hasattr(os, 'link'):
                    os.link(pre_lock.name, f.name + '.lock')
                    dotlock_done = True
                    os.unlink(pre_lock.name)
                else:
                    os.rename(pre_lock.name, f.name + '.lock')
                    dotlock_done = True
            except OSError, e:
                if e.errno != errno.EEXIST: raise

            if time() - start_dotlock > MAX_DOTLOCK_WAIT:
                raise IOError, 'Timed-out while waiting for dot-lock'

    except:
        if fcntl:
            fcntl.lockf(f, fcntl.LOCK_UN)
        if dotlock_done:
            os.remove(f.name + '.lock')
        raise

def _create_temporary(path):
    """Create a temp file based on path and open for reading and writing."""
    file_name = '%s.%s.%s' % (path, int(time()), os.getpid())
    fd = os.open(file_name, os.O_CREAT | os.O_EXCL | os.O_RDWR)
    try:
        return open(file_name, 'rb+')
    finally:
        os.close(fd)

def _unlock_file(f):
    """Unlock file f using lockf and dot locking."""
    if fcntl:
        fcntl.lockf(f, fcntl.LOCK_UN)
    if os.path.exists(f.name + '.lock'):
        os.remove(f.name + '.lock')

def _message_factory(fp):
    try:
        return email.message_from_file(fp)
    except email.Errors.MessageParseError:
        # Don't return None since that will
        # stop the mailbox iterator
        return ''

###################################################
# Classes and Utilities for Roundup Installations #
###################################################

# XXX This has to come back into use.
def _aliasAlreadyInUse(v):
    allInstalls  = conf.supybot.plugins.Roundup.roundups._children
    allAliases = allInstalls.keys()[:]
    for name, group in allInstalls.iteritems():
        allAliases.extend(group.aliases())
    # XXX Somehow we have to exclude the installation we're
    # modifying.
    if v in allAliases: return True
    return False

class RoundupName(registry.String):
    """Roundup names must contain only alphabetical characters
    and must not already be in use by some other installation."""
    def setValue(self, v):
        v = v.lower()
        if not re.match('\w+$', v):
            self.error()
        registry.String.setValue(self, v)

class RoundupNames(registry.SpaceSeparatedListOfStrings):
    Value = RoundupName

def registerRoundup(name, url='', username='', password=''):
    if (not re.match('\w+$', name)):
        s = utils.str.normalizeWhitespace(RoundupName.__doc__)
        raise registry.InvalidRegistryValue("%s (%s)" % (s, name))

    install = conf.registerGroup(conf.supybot.plugins.Roundup.roundups,
                                 name.lower())
    conf.registerGlobalValue(install, 'url',
        registry.String(url, """Determines the URL to this Roundup
        installation. This must be identical to the urlbase (or sslbase)
        parameter used by the installation. (The url that shows up in 
        emails.) It must end with a forward slash."""))
    conf.registerGlobalValue(install, 'username', 
        registry.String(username, """The username to log in as, if any. Note that
        without a username, some data will be unavailable (like correlating
        user IDs with usernames).""", private=True))
    conf.registerGlobalValue(install, 'password', 
        registry.String(password, """The password to log in with, if any.""",
            private=True))
    conf.registerChannelValue(install, 'queryTerms',
        registry.String('',
        """Additional search terms in space-separated format, that will be added to
        every search done with "query" against this installation."""))
#    conf.registerGlobalValue(install, 'aliases',
#        RoundupNames([], """Alternate names for this Roundup
#        installation. These must be globally unique."""))

    conf.registerGroup(install, 'watchedItems', orderAlphabetically=True)
    conf.registerChannelValue(install.watchedItems, 'product',
        registry.CommaSeparatedListOfStrings([],
        """What products should be reported to this channel?"""))
    conf.registerChannelValue(install.watchedItems, 'component',
        registry.CommaSeparatedListOfStrings([],
        """What components should be reported to this channel?"""))
    conf.registerChannelValue(install.watchedItems, 'changer',
        registry.SpaceSeparatedListOfStrings([],
        """Whose changes should be reported to this channel?"""))
    conf.registerChannelValue(install.watchedItems, 'all',
        registry.Boolean(False,
        """Should *all* changes be reported to this channel?"""))

    conf.registerChannelValue(install, 'reportedChanges',
        registry.CommaSeparatedListOfStrings(['newBug', 'newAttach', 'Flags',
        'Attachment Flags', 'Resolution', 'Product', 'Component'],
        """The names of fields, as they appear in bugmail, that should be
        reported to this channel."""))
    
    conf.registerGroup(install, 'traces')
    conf.registerChannelValue(install.traces, 'report',
        registry.Boolean(False, """Some Roundup installations have gdb
        stack traces in comments. If you turn this on, the bot will report
        some basic details of any trace that shows up in the comments of
        a new bug."""))
    conf.registerChannelValue(install.traces, 'ignoreFunctions',
        registry.SpaceSeparatedListOfStrings(['__kernel_vsyscall', 'raise',
        'abort', '??'], """Some functions are useless to report, from a stack trace.
        This contains a list of function names to skip over when reporting
        traces to the channel."""))
    #conf.registerChannelValue(install.traces, 'crashStarts',
    #    registry.CommaSeparatedListOfStrings([],
    #    """These are function names that indicate where a crash starts
    #    in a stack trace."""))
    conf.registerChannelValue(install.traces, 'frameLimit',
        registry.PositiveInteger(5, """How many stack frames should be
        reported from the crash?"""))

class RoundupNotFound(registry.NonExistentRegistryEntry):
    pass

class RoundupInstall:
    """Represents a single Roundup."""

    '''Words that describe each flag status except "requested."'''
    status_words = { '+' : 'granted', '-' : 'denied', 
                     'cancelled' : 'cancelled' }

    def __init__(self, plugin, name):
        try:
            self.conf = conf.supybot.plugins.Roundup.roundups.get(name.lower())
        except registry.NonExistentRegistryEntry:
            raise RoundupNotFound, 'No Roundup called %s' % name
        self.url  = self.conf.url()
        self.name = name
        #self.aliases = self.conf.aliases()
        #self.aliases.append(name)
        self.plugin = plugin
        self.opener = urllib2.build_opener(urllib2.HTTPCookieProcessor())
        urllib2.install_opener(self.opener)
        if self.conf.username():
            p = urllib.urlencode({
                '__login_name': self.conf.username(),
                '__login_password': self.conf.password(),
                '@action': 'Login'
                })
            f = self.opener.open(self.url, p)
            data = f.read()
            f.close()

    def query(self, terms, total, channel, limit=None):
        # Build the query URL
        baseTerms = self.plugin.registryValue('roundups.%s.queryTerms' \
                                              % self.name , channel)
        fullTerms = "%s %s" % (terms, baseTerms)
        fullTerms = fullTerms.strip()
        fullTerms = '+'.join(fullTerms.split(' '))
        queryurl = '%sissue?@action=export_csv&@columns=id&@search_text=%s' \
                   % (self.url, urllib.quote(fullTerms))
        
        self.plugin.log.debug('Query: %s' % queryurl)

        bug_csv_stream = self.opener.open(queryurl)
        if not bug_csv_stream:
             raise callbacks.Error, 'Got empty CSV'

        dict_reader = csv.DictReader(bug_csv_stream)
        bug_ids = [int(thing['id']) for thing in dict_reader]
        self.plugin.log.debug('Bug IDs: %r' % bug_ids)

        if not bug_ids:
            return ['No results for "%s."' % terms]

        if total:
            return ['%d results for "%s."' % (len(bug_ids), terms)]
        else:
            if limit:
                bug_ids = bug_ids[:limit]
            return self.getBugs(bug_ids, channel)

    def getAttachments(self, attach_ids, channel):
        # The code for getting the title is copied from the Web plugin
        attach_url = '%sfile%s'
        attach_bugs = {}
        lines = []

        # Get the bug ID that each bug is on.
        for attach_id in attach_ids:
            my_url = attach_url % (self.url, attach_id)
            text_stream = self.opener.open(my_url)
            text = text_stream.read()
            text_stream.close()
            match  = re.search('link</td><td><a href=\"issue(\d+)', text)
            if not match:
                err = 'Attachment %s was not found or is not accessible.' \
                       % attach_id
                lines.append(self.plugin._formatLine(err, channel, 'attachment'))
                continue
            bug_id = match.group(1)
            if bug_id not in attach_bugs:
                attach_bugs[bug_id] = []
            attach_bugs[bug_id].append(attach_id)

        # Get the attachment details
        for bug_id, attachments in attach_bugs.iteritems():
            self.plugin.log.debug('Getting attachments %r on bug %s' % \
                                  (attachments, bug_id))
            attach_strings = self.getAttachmentsOnBug(attachments,
                                 bug_id, channel, do_error=True)
            lines.extend(attach_strings)
        return lines

    def getBugs(self, ids, channel, show_url=True):
        """Returns an array of formatted strings describing the bug ids,
        using preferences appropriate to the passed-in channel."""

        bugs = self._getBugCsv(ids, channel)
        bug_strings = [];
        for bug in bugs:
            self.plugin.log.debug('Bug items: %s' % bug.items())
            bug_id = bug['id']
            if show_url:
                bug_url = '%sissue%s' \
                          % (self.url, bug_id)
            else:
                bug_url = bug_id

            if hasattr(bug, 'error'):
                bug_strings.append(self._bugError(bug, bug_url))
            else:
                bug_data = []
                for field in self.plugin.registryValue('columns.issue', channel):
                    field_text = self._field_text_to_string(bug, field, channel)
                    if field_text and field != 'id':
                        bug_data.append(field + ': ' + field_text)
                bug_strings.append('Bug ' + bug_url + ' - ' + \
                                   ', '.join(bug_data))

        bug_strings = [self.plugin._formatLine(s, channel, 'bug') \
                       for s in bug_strings]
        return bug_strings

    def getAttachmentsOnBug(self, attach_ids, bug_id, channel, do_error=False):
        bug = [thing for thing in self._getBugCsv([bug_id], channel)][0]
        if hasattr(bug, 'error'):
            if do_error:
                return [self._bugError(bug, bug_id)]
            else:
                return []

        # Sometimes we're passed ints, sometimes strings. We want to always
        # have a list of ints so that "in" works below.
        attach_ids = [int(id) for id in attach_ids]
        attachments = self._getAttachmentCsv(attach_ids, channel)
        attach_strings = []
        for attachment in attachments:
            self.plugin.log.debug('Attachment items: %s' % attachment.items())
            attach_id = int(attachment['id'])
            if attach_id not in attach_ids: continue

            attach_url = '%sfile%s' % (self.url, attach_id)
            attach_data = []
            for field in self.plugin.registryValue('columns.file', channel):
                field_text = attachment[field]
                if field_text and field != 'id':
                    attach_data.append(field + ': ' + field_text)
            attach_strings.append('Attachment ' + attach_url + ' - ' \
                                  + ', '.join(attach_data))
        attach_strings = [self.plugin._formatLine(s, channel, 'attachment') \
                          for s in attach_strings]
        return attach_strings

    def handleBugmail(self, bug):
        # Add the status into the resolution if they both changed.
        diffs = bug.diffs()
        resolution = bug.changed('Resolution')
        status     = bug.changed('Status')
        if status and resolution:
            status     = status[0]
            resolution = resolution[0]
            if resolution['added']:
                status['added'] = status['added'] + ' ' \
                                 + resolution['added']
            if resolution['removed']:
                status['removed'] = status['removed'] + ' ' \
                                    + resolution['removed']
                    
        for irc in world.ircs:
            for channel in irc.state.channels.keys():
                if self._shouldAnnounceBugInChannel(bug, channel):
                    try:
                        self._handleBugmailForChannel(bug, irc, channel)
                    except:
                        self.plugin.log.exception(\
                        'Exception while handling mail for bug %s on %s.%s'\
                        % (bug.bug_id, irc.network, channel))
                    # Let other threads run, when we're processing lots
                    # of mail.
                    sleep(0.01)

    #######################################
    # Bugmail Handling: Major Subroutines #
    #######################################

    def _handleBugmailForChannel(self, bug, irc, channel):
        self.plugin.log.debug('Handling bugmail in channel %s.%s' \
                      % (irc.network, channel))
        report = self.reportFor(channel)

        # Get the lines we should say about this bugmail
        lines = []
        say_attachments = []
        if 'newBug' in report and bug.new:
            new_msg = self.plugin.registryValue('messages.newBug', channel)
            lines.append(new_msg % bug.fields())
        if 'newAttach' in report and bug.attach_id:
            attach_msg = self.plugin.registryValue(\
                                        'messages.newAttachment', channel)
            lines.append(attach_msg % bug.fields())
            if self.plugin._shouldSayAttachment(bug.attach_id, channel):
                say_attachments.append(bug.attach_id)

        for diff in bug.diffs():
            if not self._shouldAnnounceChangeInChannel(diff, channel):
                continue
            
            # If we're watching both status and resolution, and both
            # change, don't say Status--say resolution instead.
            if (('Resolution' in report or 'All' in report)
                and bug.changed('Resolution')
                and bug.changed('Status')):
                if diff['what'] == 'Status': continue
                if diff['what'] == 'Resolution': 
                    diff = bug.changed('Status')[0]

            if ('attachment' in diff
                # This is a bit of a hack.
                and self.plugin._shouldSayAttachment(diff['attachment'],
                                                     channel)):
                say_attachments.append(diff['attachment'])

            bug_messages = self._diff_messages(channel, bug, diff)
            lines.extend(bug_messages)
            
        # Do the formatting for changes
        lines = [self.plugin._formatLine(l, channel, 'change') \
                 for l in lines]

        if (bug.new and bug.comment and self.plugin.registryValue(\
            'roundups.%s.traces.report' % self.name, channel)):
            try:
                trace = traceparser.Trace(bug.comment)
                line = self._traceLine(trace, channel)
                if line: lines.append(line)
            except traceparser.NoTrace:
                pass
            except:
                self.plugin.log.exception('Exception while parsing trace:')

        # If we have anything to say in this channel about this
        # bug, then say it.
        if lines:
            self.plugin.log.debug('Reporting %d change(s) to %s' \
                                  % (len(lines), channel))
            if say_attachments:
                attach_strings = self.getAttachmentsOnBug(say_attachments, \
                    bug.bug_id, channel)
                lines.extend(attach_strings)
            if self.plugin._shouldSayBug(bug.bug_id, channel):
                lines.extend(self.getBugs([bug.bug_id], channel))
            if bug.dupe_of and self.plugin._shouldSayBug(bug.dupe_of, channel): 
                lines.extend(self.getBugs([bug.dupe_of], channel))
            for line in lines:
                self._send(irc, channel, line)
                
    def _diff_messages(self, channel, bm, diff):
        lines = []

        attach_string = ''
        if diff.get('attachment', None):
            attach_string = ' for attachment ' + diff['attachment']

        bug_string = '%s on bug %d' % (attach_string, bm.bug_id)
        if 'flags' in diff:
            flags = diff['flags']
            for status, word in self.status_words.iteritems():
                for flag in flags[status]:
                    # Cancelled flags show up like review?somebody
                    if status == 'cancelled':
                        flag_name = "%(name)s%(status)s" % flag
                        if flag['requestee']:
                            flag_name = "%s(%s)" \
                                         % (flag_name, flag['requestee'])
                    else:
                        flag_name = flag['name']

                    lines.append('%s %s %s%s.' % (bm.changer, word, 
                                                  flag_name, bug_string))
            for flag in flags['?']:
                requestee = self.plugin.registryValue('messages.noRequestee', channel)
                if flag['requestee']: 
                    requestee = 'from ' + flag['requestee']
                lines.append('%s requested %s %s%s.' % (bm.changer,
                             flag['name'], requestee, bug_string))
        else:
            what    = diff['what']
            removed = diff['removed']
            added   = diff['added']

            line = bm.changer
            if what in bugmail.MULTI_FIELDS:
                if added:             line += " added %s to" % added
                if added and removed: line += " and"
                if removed:           line += " removed %s from" % removed
                line += " the %s field%s." % (what, bug_string)
            elif (what in ['Resolution', 'Status'] and added.find('DUPLICATE') != -1):
                line += " marked bug %d as a duplicate of bug %d." % \
                        (bm.bug_id, bm.dupe_of)
            # We only added something.
            elif not removed:
                line += " set the %s field%s to %s." % (what, bug_string, added)
            # We only removed something
            elif not added:
                line += " cleared the %s '%s'%s." % (what, removed, bug_string)
            # We changed the value of a field from something to 
            # something else
            else:
                line += " changed the %s%s from %s to %s." % \
                        (what, bug_string, removed, added)

            lines.append(line)
        return lines
        
    def _traceLine(self, trace, channel):
        self.plugin.log.debug('Making line for trace: %r' % trace)
        usedThread = trace.threads[0]
        fIndex = 0
        interesting = False
        for thread in trace.threads:
            fIndex = thread.signalHandlerIndex()
            if fIndex > -1:
                usedThread = thread
                interesting = True
                break
            
        if not interesting: fIndex = 0
            #for f in self.plugin.registryValue('roundups.%s.traces.crashStarts'
            #                                   % self.name, channel):
            #    fIndex = thread.functionIndex(f)
                
        funcs = []
        maxFrames = self.plugin.registryValue(\
            'roundups.%s.traces.frameLimit' % self.name, channel)
        ignoreFuncs = self.plugin.registryValue(\
            'roundups.%s.traces.ignoreFunctions' % self.name, channel)
        usedFrames = 0
        for frame in usedThread[fIndex:]:
            if frame.function() == '' or frame.function() in ignoreFuncs:
                continue
            funcs.append(frame.function())
            usedFrames = usedFrames + 1
            if usedFrames >= maxFrames: break
        line = 'Trace:'
        if trace.bin:
            line = "%s %s ->" % (line, trace.bin)
        line = "%s %s" % (line, ', '.join(funcs))
        if not interesting:
            line = line + ' (Possibly not interesting)'
        return line
    
    ########################################
    # Bugmail Handling: Helper Subroutines #
    ########################################
    
    def _send(self, irc, channel, line):
        msg = ircmsgs.privmsg(channel, line)
        irc.queueMsg(msg)
   
    def reportFor(self, channel):
        return self.plugin.registryValue('roundups.%s.reportedChanges' \
                                         % self.name, channel)
   
    def _shouldAnnounceBugInChannel(self, bug, channel):
        if self.plugin.registryValue('roundups.%s.watchedItems.all' \
                                     % self.name, channel):
            return True
        
        # If something was just removed from a particular field, we
        # want to still report that change in the proper channel.
        field_values = bug.fields()
        for field in field_values.keys():
            array = [field_values[field]]
            old_item = bug.changed(field)
            if old_item:
                array.append(old_item[0]['removed'])
            field_values[field] = array

        for field, array in field_values.iteritems():
            for value in array:
                # Check the configuration for this product, component,
                # etc.
                try:
                    watch_list = self.plugin.registryValue(
                        'roundups.%s.watchedItems.%s' % (self.name, field), channel)
                    if value in watch_list: return True
                except registry.NonExistentRegistryEntry:
                    continue
                except: raise
                
        return False

    def _shouldAnnounceChangeInChannel(self, diff, channel):
        if ('All' in self.reportFor(channel)
            or diff['what'] in self.reportFor(channel)):
            return True
        return False

    ##############################
    # General Helper Subroutines #
    ##############################

    def _field_text_to_string(self, obj, field, channel):
        if obj[field] and obj[field] != 'None':
            field_lookup_list = self.plugin.registryValue('fieldLookupList', channel)

            for field_lookup in field_lookup_list:
                # Get current field, class and classname in the list.
                (lfield, lclass, lclassfield) = field_lookup.split('.')
                # See if we have found our field.
                if field.lower() == lfield:
                    # Found it, so get appropriate data and return appropriate
                    # field.
                    field_csv = self._getCsvByIds(lclass, [obj[field]], channel)
                    return "%s" % [thing for thing in field_csv][0][lclassfield]

        # Didn't find the field so doesn't need reworking.
        return '%s' % obj[field]

    def _getBugCsv(self, ids, channel):
        return self._getCsvByIds('issue', ids, channel)

    def _getAttachmentCsv(self, ids, channel):
        return self._getCsvByIds('file', ids, channel)

    def _getCsvByIds(self, classname, ids, channel):
        columns = self.plugin.registryValue('columns.%s' % classname, channel)
        # Somethimes ids can be a list of ints or strings, so we use list
        # comprehension to safeguard join().
        queryurl = self.url + classname \
                   + '?@action=export_csv&@filter=id' \
                   + '&@columns=' + ','.join(columns) \
                   + '&id=' + ','.join(['%s' % thing for thing in ids])

        self.plugin.log.debug('Getting %ss from %s' % (classname, queryurl))

        querycsv_stream = self.opener.open(queryurl)
        if not querycsv_stream:
            raise callbacks.Error, 'Got empty %s content' % classname
       
        try: 
            return csv.DictReader(querycsv_stream)
        except Exception:
            return []

    def _bugError(self, bug, bug_url):
        error_type = getattr(bug, 'error')
        if error_type == 'NotFound':
            return 'Bug %s was not found.' % bug_url
        elif error_type == 'NotPermitted':
            return 'Bug %s is not accessible.' % bug_url
        return 'Bug %s could not be retrieved: %s' % (bug_url,  error_type)

##########
# Plugin #
##########

class Roundup(callbacks.PluginRegexp):
    """This plugin provides the ability to interact with Roundup installs.
    It can report changes from multiple Roundups by parsing emails, and it can
    report the details of bugs and attachments to your channel."""

    threaded = True
    callBefore = ['URL', 'Web']
    regexps = ['snarfBugUrl']
    unaddressedRegexps = ['snarfBug']

    def __init__(self, irc):
        self.__parent = super(Roundup, self)
        self.__parent.__init__(irc)
        self.saidBugs = ircutils.IrcDict()
        self.saidAttachments = ircutils.IrcDict()
        sayTimeout = self.registryValue('bugSnarferTimeout')
        for k in irc.state.channels.keys():
            self.saidBugs[k] = TimeoutQueue(sayTimeout)
            self.saidAttachments[k] = TimeoutQueue(sayTimeout)
        #period = self.registryValue('mboxPollTimeout')
        #schedule.addPeriodicEvent(self._pollMbox, period, name=self.name(),
        #                          now=False)
        for name in self.registryValue('roundups'):
            registerRoundup(name)
        reload(sys)
        sys.setdefaultencoding('utf-8')

    def die(self):
        self.__parent.die()
        #schedule.removeEvent(self.name())

    def add(self, irc, msg, args, name, url, username='', password=''):
        """<name> <url> [<username>] [<password>]
        Lets the bot know about a new Roundup installation that it can
        interact with. Name is the name that you use most commonly to refer
        to this installation--it must not have any spaces. URL is the
        urlbase (or sslbase, if the installation uses that) of the
        installation."""

        registerRoundup(name, url, username, password)
        roundups = self.registryValue('roundups')
        roundups.append(name.lower())
        self.setRegistryValue('roundups', roundups)
        irc.replySuccess()
    add = wrap(add, ['admin', 'somethingWithoutSpaces','url'])
             
    def attachment(self, irc, msg, args, attach_ids):
        """<attach_id> [<attach_id>]+
        Reports the details of the attachment with that id to this channel.
        Accepts a space-separated list of ids if you want to report the details
        of more than one attachment."""

        channel = msg.args[0]
        installation = self._defaultRu(channel)
        lines = installation.getAttachments(attach_ids, channel)
        for l in lines: irc.reply(l)
    attachment = wrap(attachment, [many(('id','attachment'))])
    file = attachment

    def bug(self, irc, msg, args, bug_id_string):
        """<bug_id> [<bug_ids>]
        Reports the details of the bugs with the listed ids to this channel.
        Accepts bug aliases as well as numeric ids. Your list can be separated
        by spaces, commas, and the word "and" if you want."""

        channel = msg.args[0]
        bug_ids = re.split('[!?.,\(\)\s]|[\b\W]and[\b\W]*|\bbug\b', 
                           bug_id_string)
        installation = self._defaultRu(channel)
        bug_strings = installation.getBugs(bug_ids, channel)
        for s in bug_strings:
            irc.reply(s)
    bug = wrap(bug, ['text'])
    issue = bug

    def query(self, irc, msg, args, options, query_string):
        """[--total] [--install=<install name>] <search terms>
        Searches your Roundup using the QuickSearch syntax, and returns
        a certain number of results.
        
        --install specifies the name of an installation to search, instead
        of using the channel's default installation.
        
        If you specify --total, it will return the total number of results
        found, instead of the actual results."""

        channel = msg.args[0]
        total   = False
        installation = self._defaultRu(channel)
        for opt in options:
            if opt[0] == 'total': total = True
            if opt[0] == 'install':
                name = opt[1]
                try:
                    installation = RoundupInstall(self, name)
                except RoundupNotFound:
                    irc.error("No install named '%s'" % name)
                    return
        
        limit = self.registryValue('queryResultLimit', channel)
        strings = installation.query(query_string, total, channel, limit)
        for s in strings: irc.reply(s)
        
    query = wrap(query, [getopts({'total' : '', 'install' : 'something'}), 'text'])

    def snarfBug(self, irc, msg, match):
        r"""\b((?P<install>\w+)\b\s*)?(?P<type>bug|issue|attachment|file)(\b[\s#]*|\B)(?P<id>\d+)"""
        channel = msg.args[0]
        if not self.registryValue('bugSnarfer', channel): return

        id_matches = match.group('id').split()
        type = match.group('type')
        ids = []
        self.log.debug('Snarfed ID(s): ' + ' '.join(id_matches))
        # Check if the bug has been already snarfed in the last X seconds
        for id in id_matches:
            if type.lower() == 'bug' or type.lower() == 'issue':
                should_say = self._shouldSayBug(id, channel)
            else: 
                should_say = self._shouldSayAttachment(id, channel)

            if should_say:
                ids.append(id)
        if not ids: return

        self.log.debug('Install: %r' % match.group('install'))
        installation = self._ruOrDefault(match.group('install'), channel)
        if type.lower() == 'bug' or type.lower() == 'issue':
            strings = installation.getBugs(ids, channel)
        else: 
            strings = installation.getAttachments(ids, channel)

        for s in strings:
            irc.reply(s, prefixNick=False)

    def snarfBugUrl(self, irc, msg, match):
        r"(?P<url>https?://\S+/)issue(?P<bug>\d+)"
        channel = msg.args[0]
        if (not self.registryValue('bugSnarfer', channel)): return

        url = match.group('url')
        bug_ids =  match.group('bug').split()
        self.log.debug('Snarfed Bug ID(s) from URL(s): ' + ' '.join(bug_ids))
        try:
            installation = self._ruByUrl(url)
        except RoundupNotFound:
            installation = self._defaultRu(channel)
        bug_strings = installation.getBugs(bug_ids, channel, show_url=False)
        for s in bug_strings:
            irc.reply(s, prefixNick=False)
    
    def _ruOrDefault(self, name, channel):
        if name is None:
            return self._defaultRu(channel)
        
        try:
            bz = RoundupInstall(self, name)
        except RoundupNotFound:
            bz = self._defaultRu(channel)
            
        return bz
    
    def _defaultRu(self, channel=None):
        name = self.registryValue('defaultRoundup', channel)
        return RoundupInstall(self, name)
            
    def _ruByUrl(self, url):
        domainMatch = re.match('https?://(\S+)/', url, re.I)
        domain = domainMatch.group(1)
        installs = self.registryValue('roundups', value=False)
        for name, group in installs._children.iteritems():
            if group.url().lower().find(domain.lower()) > -1:
                return RoundupInstall(self, name)
        raise RoundupNotFound, 'No Roundup with URL %s' % url
        
    def _formatLine(self, line, channel, type):
        """Implements the 'format' configuration options."""
        format = self.registryValue('format.%s' % type, channel)
        already_colored = False
        for item in format:
            if item == 'bold':
                line = ircutils.bold(line)
            elif item == 'reverse':
                line = ircutils.reverse(line)
            elif item == 'underlined':
                line = ircutils.underline(line)
            elif already_colored:
                line = ircutils.mircColor(line, bg=item)
            elif item != '':
                line = ircutils.mircColor(line, fg=item)
        return line

    def _shouldSayBug(self, bug_id, channel):
        if channel not in self.saidBugs:
            sayTimeout = self.registryValue('bugSnarferTimeout')
            self.saidBugs[channel] = TimeoutQueue(sayTimeout)
        if bug_id in self.saidBugs[channel]:
            return False

        self.saidBugs[channel].enqueue(bug_id)
        #self.log.debug('After checking bug %s queue is %r' \
        #                % (bug_id, self.saidBugs[channel]))
        return True

    def _shouldSayAttachment(self, attach_id, channel):
        if channel not in self.saidAttachments:
            sayTimeout = self.registryValue('bugSnarferTimeout')
            self.saidAttachments[channel] = TimeoutQueue(sayTimeout)
        if attach_id in self.saidAttachments[channel]:
            return False
        self.saidAttachments[channel].enqueue(attach_id)
        return True

    def _pollMbox(self):
#        return
#    
#    def poll(self, irc, msg, args):
        file_name = self.registryValue('mbox')
        if not file_name: return
        boxFile = open(file_name, 'r+b')
        _lock_file(boxFile)
        self.log.debug('Polling mbox %r' % boxFile)

        try:
            box = mailbox.PortableUnixMailbox(boxFile, _message_factory)
            bugmails = []
            for message in box:
                if message == '': continue
                self.log.debug('Parsing message %s' % message['Message-ID'])
                try:
                    bugmails.append(bugmail.Bugmail(message))
                except bugmail.NotBugmailException:
                    continue
                except:
                    self.log.exception('Exception while parsing message:')
                    self.log.debug("Message:\n%s" % message.as_string())
            boxFile.truncate(0)
        finally:
            _unlock_file(boxFile)
            boxFile.close()

        self._handleBugmails(bugmails)
    
    def _handleBugmails(self, bugmails):
        for mail in bugmails:
            try:
                installation = self._ruByUrl(mail.urlbase)
            except RoundupNotFound:
                installation = self._defaultRu()
            self.log.debug('Handling bugmail for bug %s on %s (%s)' \
                           % (mail.bug_id, mail.urlbase, installation.name))
            installation.handleBugmail(mail)

Class = Roundup

# vim:set shiftwidth=4 tabstop=4 expandtab textwidth=79:
