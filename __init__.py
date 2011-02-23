###
# Copyright (c) 2007, Max Kanat-Alexander
# Copyright (c) 2011, Jack Grigg
# All rights reserved.
#
#
###

"""
Interact with Roundup installations.
"""

import supybot
import supybot.world as world

# Use this for the version of this plugin.  You may wish to put a CVS keyword
# in here if you're keeping the plugin in CVS or some similar system.
__version__ = "0.1.0.0"

# XXX Replace this with an appropriate author or supybot.Author instance.
__author__ = supybot.Author('Jack Grigg', 'pythonian4000', 'me@jackgrigg.com')

# This is a dictionary mapping supybot.Author instances to lists of
# contributions.
__contributors__ = {
        supybot.Author('Max Kanat-Alexander', 'mkanat',
        'mkanat@bugzilla.org'): ['Bugzilla plugin this is based on'],
        supybot.Author('Jack Grigg', 'pythonian4000',
        'me@jackgrigg.com'): ['Conversion to and development of Roundup plugin'],
        }

# This is a url where the most recent plugin package can be downloaded.
__url__ = 'http://git.jackgrigg.com/supybot-roundup/'

import config
import plugin
import bugmail
import traceparser
reload(plugin) # In case we're being reloaded.
reload(bugmail)
reload(traceparser)

# Add more reloads here if you add third-party modules and want them to be
# reloaded when this plugin is reloaded.  Don't forget to import them as well!

if world.testing:
    import test

Class = plugin.Class
configure = config.configure


# vim:set shiftwidth=4 tabstop=4 expandtab textwidth=79:
