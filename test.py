###
# Copyright (c) 2007, Max Kanat-Alexander
# Copyright (c) 2011, Jack Grigg
# All rights reserved.
#
#
###

from supybot.test import *

class RoundupTestCase(ChannelPluginTestCase):
    plugins = ('Roundup')
    config = {'supybot.plugins.Roundup.mbox': 'test/mbox'
              'supybot.plugins.Roundup.reportedChanges': ['All', 'newBug', 
                                                           'newAttach']}


# vim:set shiftwidth=4 tabstop=4 expandtab textwidth=79:
