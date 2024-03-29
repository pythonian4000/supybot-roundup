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


import supybot.conf as conf
import supybot.registry as registry
import supybot.ircutils as ircutils

def configure(advanced):
    # This will be called by supybot to configure this module.  advanced is
    # a bool that specifies whether the user identified himself as an advanced
    # user or not.  You should effect your configuration by manipulating the
    # registry as appropriate.
    from supybot.questions import expect, anything, something, yn
    conf.registerPlugin('Roundup', True)
    if yn("""This plugin can show data about bug URLs and numbers mentioned
             in the channel. Do you want this bug snarfer enabled by 
             default?""", default=False):
        conf.supybot.plugins.Roundup.bugSnarfer.setValue(True)


class ColorString(registry.OnlySomeStrings):
    """That is not a valid color/format string."""
    validStrings = ircutils.mircColors.keys()
    validStrings.extend(['bold', 'reverse', 'underlined', ''])

class FormatString(registry.CommaSeparatedListOfStrings):
    Value = ColorString
    
class ValidInstall(registry.String):
    """You must pick the name of a group from the list of roundups."""
    
    def setValue(self, v):
        names  = conf.supybot.plugins.Roundup.roundups()[:]
        names.append('')
        if v not in names:
            self.error()
        registry.String.setValue(self, v)

Roundup = conf.registerPlugin('Roundup')
# This is where your configuration variables (if any) should go.  For example:
# conf.registerGlobalValue(Roundup, 'someConfigVariableName',
#     registry.Boolean(False, """Help for someConfigVariableName."""))
conf.registerChannelValue(Roundup, 'bugSnarfer',
    registry.Boolean(False, """Determines whether the bug snarfer will be
    enabled, such that any Roundup URLs and bug ### seen in the channel
    will have their information reported into the channel."""))
conf.registerGlobalValue(Roundup, 'bugSnarferTimeout',
    registry.PositiveInteger(300, 
    """Users often say "bug XXX" several times in a row, in a channel.
    If "bug XXX" has been said in the last (this many) seconds, don't
    fetch its data again. If you change the value of this variable, you
    must reload this plugin for the change to take effect."""))

conf.registerGroup(Roundup, 'columns',
    help="""What columns should be fetched for various Roundup classes""")
conf.registerChannelValue(Roundup.columns, 'issue',
    registry.SpaceSeparatedListOfStrings(['id', 'activity',
        'title', 'creator', 'assignedto', 'status'],
    """The fields to list when describing a bug, after the URL."""))
conf.registerChannelValue(Roundup.columns, 'file',
    registry.SpaceSeparatedListOfStrings(['id', 'type', 'name'],
    """The fields to list when describing an attachment after announcing
    a change to that attachment."""))
conf.registerChannelValue(Roundup.columns, 'user',
    registry.SpaceSeparatedListOfStrings(['id', 'username', 'realname'],
    """The fields to list when describing a user."""))
conf.registerChannelValue(Roundup.columns, 'status',
    registry.SpaceSeparatedListOfStrings(['id', 'name'],
    """The fields to list when describing a user."""))
conf.registerChannelValue(Roundup.columns, 'milestone',
    registry.SpaceSeparatedListOfStrings(['id', 'name'],
    """The fields to list when describing a user."""))

conf.registerChannelValue(Roundup, 'fieldLookupList',
    registry.SpaceSeparatedListOfStrings([
        'creator.user.username',
        'assignedto.user.username',
        'status.status.name'],
    """A list of fields for which their string value should be looked up.
    Format is field.class.classfield (where classfield is the field in the
    class containing the desired string value). Note that you can only name
    classes here which have had their columns/class fields set in config.py;
    this can be added to but it will require that the bot be restarted."""))

conf.registerGroup(Roundup, 'format',
    help="""How various messages should be formatted in terms of bold, colors,
         etc.""")
conf.registerChannelValue(Roundup.format, 'change',
    FormatString(['teal'], 
    """When the plugin reports that something has changed on a
                        bug, how should that string be formatted?"""))
conf.registerChannelValue(Roundup.format, 'attachment',
    FormatString(['green'], 
    """When the plugin reports the details of an attachment, how should we
    format that string?"""))
conf.registerChannelValue(Roundup.format, 'bug',
    FormatString(['red'], 
   """When the plugin reports the details of a bug, how should we format 
   that string?"""))

conf.registerChannelValue(Roundup, 'queryResultLimit',
    registry.PositiveInteger(5, 
    """The number of results to show when using the "query" command."""))

conf.registerGlobalValue(Roundup, 'mbox', 
    registry.String('', """A path to the mbox that we should be watching for
    bugmail.""", private=True))
conf.registerGlobalValue(Roundup, 'mboxPollTimeout',
    registry.PositiveInteger(10, """How many seconds should we wait between
    polling the mbox?"""))

conf.registerGroup(Roundup, 'messages', orderAlphabetically=True, 
    help="""Various messages that can be re-formatted as you wish. If a message
            takes a format string, the available format variables are:
            product, component, bug_id, attach_id, and changer)""")

conf.registerChannelValue(Roundup.messages, 'newBug',
    registry.String("New %(product)s bug %(bug_id)d filed by %(changer)s.",
    """What the bot will say when a new bug is filed."""))
conf.registerChannelValue(Roundup.messages, 'newAttachment',
    registry.String("%(changer)s added attachment %(attach_id)d to bug %(bug_id)d",
    """What the bot will say when somebody adds a new attachment to a bug."""))
conf.registerChannelValue(Roundup.messages, 'noRequestee',
    registry.String('from the wind',
    """How should we describe it when somebody requests a flag without
    specifying a requestee? This should probably start with "from." It
    can also be entirely empty, if you want."""))

conf.registerGlobalValue(Roundup, 'roundups',
    registry.SpaceSeparatedListOfStrings([],
    """The various Roundup installations that have been created
    with the 'add' command."""))
conf.registerChannelValue(Roundup, 'defaultRoundup',
        ValidInstall('', """If commands don't specify what installation to use,
        then which installation should we use?"""))

# vim:set shiftwidth=4 tabstop=4 expandtab textwidth=79:
