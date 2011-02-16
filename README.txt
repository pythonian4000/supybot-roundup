This plugin supports querying Roundup installations, showing details about
bugs, and reading bugmails sent from Roundup to show updates in an IRC
channel. It supports working with multiple Roundup installations and can
work across many channels and networks.

The main commands you'll be interested in at first are "Roundup add", and
then "query" and "bug". Then you should set the
plugins.Roundup.defaultRoundup configuration parameter.

You will probably also want to enable plugins.Roundup.bugSnarfer, which
catches the words "bug" and "attachment" in regular IRC conversation and
displays details about that bug or attachment.

The plugin has lots and lots of configuration options, and all the
configuration options have help, so feel free to read up after loading
the plugin itself, using the "config help" command.
