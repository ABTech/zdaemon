# Base level Zdaemon services
# Routing, Ping, Help
#
# Copyright (c) 2024, AB Tech
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
#
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
#
# * Neither the name of the copyright holder nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

from time import time, gmtime, strftime
import functools
import re
import socket
import subprocess
import unicodedata as ud

import cowsay

import common # For startup time
import config as cfg
import cube
from common import sendz, sendsText
from common import get_slack_thread, get_slack_channel_data, slackReact, get_slack_bot_userid
from common import hasRTLCharacters
from cube import cubeCheck
from plusplus import checkPP, getPlusplusStats, slack_plusplus_router

_PROG_ID = '$Id$'

def zdaemon_router(zclass, instance, sender, fullsender, message, triggers):
    ''' Primary router for zdaemon features '''

    # TODO: Sanitize message (e.g. backticks)
    # TODO: Log the Message?
    # print ("router: %s %s" % (zclass, instance))

    if zclass == cfg.ZDAEMON_CLASS and (instance == 'ping' or instance == 'service.query'):
        sendz(cfg.ZDAEMON_CLASS, 'ping', ping_text())
    if zclass == cfg.ZDAEMON_CLASS and instance == 'ping.help':
        sendz(cfg.ZDAEMON_CLASS, 'ping.help', pinghelp_text())

    # handle drink & duh checks
    if (zclass == cfg.ZDAEMON_CLASS or zclass == cfg.ABTECH_CLASS):
        sendCube = functools.partial(cube.sendCube, -1)
        reply = functools.partial(sendz, zclass)
        triggers.check_msg(instance, sender, message, sendCube, reply)

    # handle cubes
    cubeCheck(zclass, instance, sender, fullsender, message)

    # handle plusplus
    checkPP(zclass, instance, sender, message)


def ping_text(slack=False):
    if common.ZDAEMON_START_TIME is None:
        raise Exception("ping_text with Nonetype ZDAEMON_START_TIME?  init_common_config please!")

    # Get current uptime
    u = subprocess.Popen('uptime',
                         stdout=subprocess.PIPE,
                         encoding='ascii')
    uptime, _ = u.communicate()

    ppstats = getPlusplusStats()

    msg = "zdaemon %s\n" % _PROG_ID
    msg += "*Cubes*: %d\n" % cube.getCount()
    msg += "*Plusplus*: %d (totaling %d)\n" % (ppstats['count'], ppstats['sum'])
    msg += "*Pronouns*: she/her\n"
    msg += "*Home Address*: %s\n" % socket.gethostname()
    msg += "*Server Uptime*: %s\n" % uptime.rstrip()
    msg += "*Last Restart*: %s\n" % strftime("%a, %d %b %Y %H:%M:%S +0000", gmtime(common.ZDAEMON_START_TIME))

    lifetime_seconds = int(time()) - common.ZDAEMON_START_TIME
    lifetime_days, lifetime_seconds = divmod(lifetime_seconds, 86400)
    lifetime_hours, lifetime_seconds = divmod(lifetime_seconds, 3600)
    lifetime_minutes, lifetime_seconds = divmod(lifetime_seconds, 60)

    msg += "\nIt has been %d Days, %d Hours, %d Minutes, and %d Seconds without a restart.\n" % \
        (lifetime_days, lifetime_hours, lifetime_minutes, lifetime_seconds)

    if slack:
        msg += "\n*For more info*: Send the `!pinghelp` command, and get a response completely about zephyr.\n"
        msg += "*For actual help*: Send `!zdhelp`"
    else:
        msg += "\n*For more info*: Send a zulip message to the zdaemon stream with the subject ping.help, and get a response completely about zephyr."

    return msg


def pinghelp_text():
    msg = "zdaemon operations:\n"
    msg += "Ping: zwrite -c zdaemon -i ping -r ABTECH.ORG\n"
    msg += "\tEnsures zdaemon is operating.\n"
    msg += "Plusplus Query: zwrite -c zdaemon -i plusplus zdaemon@ABTECH.ORG -m '{foo}'\n"
    msg += "\tWhere foo is the query. Optional -{foo} syntax sorts in reverse order.\n"
    msg += "Cube Gimme: zwrite -c zdaemon -i cube.gimme -r ABTECH.ORG\n"
    msg += "\tInclude cube number, or no cube number for a random cube.\n"
    msg += "Other cube instances (replace cube.gimme):\n"
    msg += "\tcube.info: See information about last cube sent.\n"
    msg += "\tcube.sucks: Cause the cube to gain a 'sucks' vote.\n"
    msg += "\tcube.rocks: Cause the cube to lose a 'sucks' vote.\n"
    msg += "\tcube.stats: General cube statistics"
    return msg


### Slack Handling Below
def slack_ping(message):
    sendsText(message['channel'], ping_text(slack=True), thread_ts=get_slack_thread(message))


def slack_pinghelp(message):
    sendsText(message['channel'], pinghelp_text(), thread_ts=get_slack_thread(message))


def slack_zdhelp(message):
    msg = "zdaemon Slack Commands:\n"
    msg += "`!ping`: Check to see if I'm alive\n"
    msg += "`!zdhelp`: See this message again\n"
    msg += "`!ppquery {regex}`: Search the plusplus database for the given regex.  Regex must be in braces.  `-{regex}` will sort in reverse order.\n"
    msg += "`!cubegimme [num]`: Return a cube.  Optionally, provide a specific cube to view instead.\n"
    msg += "`!cubeinfo [num]`: Show metadata about the most recently sent cube.  Optionally, provide a specific cube.\n"
    msg += "`!cubesucks` and `!cuberocks`: Express your feelings about the most recent auto-sent cube.  Only available in <#%s>.\n" % cfg.SLACK_CUBE_CHANNEL_ID
    msg += "`!cubeslurp (text)`: Submit wisdom for posterity.  Leading newlines/whitespace ignored.\n"
    msg += "`!csa (text)`: Almost submit wisdom for posterity.\n"
    msg += "`!cubestats`: Show the cube scoreboard.\n"
    msg += "`!cubeactivity`: Show a history of cubes by year.\n"
    msg += "`!cubequery (string)`: Scan the cube database for the given string.\n"
    sendsText(message['channel'], msg, thread_ts=get_slack_thread(message))


def slack_gny(message):
    '''GNY only supported on slack, since slack lacks instances.

       Undocumented command.

       Who doesn't like cowsay?
    '''
    name = None
    m = re.search(r"^!gny\s+([^\s]+)\s*.*$", message["text"])

    if m:
        name = m.group(1)
    else:
        sendsText(message['channel'],
                  "If you want to gratuitously yell a name, pick a name to yell!",
                  thread_ts=get_slack_thread(message))
        return

    # If we have been asked to display RTL characters, we need to isolate literally
    # every line that cowsay produces to be LTR, as well as a special isolate around
    # the text in question.
    #
    # This is a bit of bidi black magic.  Unlike with plusplus, where we isolate based
    # on the first strong character, here the input text has _always_ been given in an
    # LTR context (since it must be preceded immediately by a strong-LTR string, '!gny'),
    # so to mirror what the command said, we're going to isolate it into an LTR context all
    # its own again, and let the codepoints fall where they may.
    #
    # Furthermore, slack-on-the-web appears to render the entire block as RTL if we don't
    # individually isolate each line of the cow (since the cow is mostly neutral), oddly,
    # slack-on-mobile doesn't appear to do this.  Either way, we isolate each line independently
    # to force LTR display.
    #
    # Note that cowsay appears to count the RTL control characters when creating its
    # speech bubble, but this error is minor and also not unique to RTL,
    # since '!gny @Foo' will do something similar, as we only count the ID, not the
    # actual name.
    hasRTL = hasRTLCharacters(name)
    if hasRTL:
        name = "\u2066%s\u2069" % (name)

    msg = cowsay.get_output_string('cow', name.upper() + ' !!!')

    rtlSafeMsg = ""
    if hasRTL:
        for line in msg.split('\n'):
            rtlSafeMsg = rtlSafeMsg + '\u2066' + line + '\u2069\n'
    else:
        rtlSafeMsg = msg

    sendsText(message['channel'], '```\n' + rtlSafeMsg + "\n```", thread_ts=get_slack_thread(message))


def slack_rip(message):
    '''RIP only supported on slack, since slack lacks instances.

       Undocumented command.

       Yes this literally only adds a reaction, since the command is really only a
       replacement for the use of the RIP zulip/zephyr instance.
    '''
    slackReact(message, "headstone")


# Handles all message events from slack.
#
# It would be great if we could use say() to respond, but we can't, since we don't always
# respond in-thread or send to the same thread, and multiple things can respond.  So, instead
# callers should used the sends utility functions to do what they need.
def zdaemon_slack_router(triggers, ack, say, message):
    # Always ack immediately, since multiple things can respond and some are slow.
    # Need to do this even if we are ignoring the message.
    ack()

    # DEBUG
    # print ("zdaemon_slack_router event: %s[%s]/%s" %
    #       (message['type'], message['channel_type'],
    #        message['subtype'] if 'subtype' in message else 'None'))


    bridge_bot_message = False
    if ('subtype' in message and
        message['subtype'] == 'bot_message' and
        'bot_id' in message):
        # Need to look up the bot and add the user id to the message.
        userid = get_slack_bot_userid(message['bot_id'])
        message['user'] = userid

        if userid == cfg.SLACK_BRIDGE_BOT_ID:
            bridge_bot_message = True

        # DEBUG
        # print("bot id: %s userid: %s" % (message['bot_id'], userid))

    # We only want to respond to real messages.  Most subtypes will be awkward for us to handle,
    # so only do true messages (no subtype) and replies for now.  (note: slack doesn't actually appear
    # to send the message_replied subtype as of Sep 2024)
    #
    # This gets rid of bot_message explicity (so we avoid loops with other bots), which is nice,
    # but also removes troublesome things like message_changed and message_deleted which would
    # need their own very specific handling.
    #
    # This does mean additional complexity is required if we want to work over a bridge, such
    # as the zulip bridge.  In the case of the bridge, we explicitly whitelist the configured
    # user id of the bridge and no other bots.
    #
    # file_share is also a type we need to handle, as if you post a picture with text,
    # that's what you get.  We ignore the attachments though.  In this case, if the event
    # has only a file and no other content, the text field will be empty (which is fine).
    #
    # thread_broadcast subtypes are sent when a message is sent both to its thread and its
    # channel.  These seem to be paried with a message_changed, which thankfully don't
    # seem to be relevant for our purposes.
    #
    # Note: You really want to do this first before any other check, since we might get a bot
    # loop via the other sanity checks below.
    if ('subtype' in message and
        (message['subtype'] not in ['thread_broadcast',
                                    'message_replied',
                                    'file_share',
                                    'bot_message'] or
         (message['subtype'] == 'bot_message' and not bridge_bot_message))):
        # DEBUG
        # print("zdaemon_slack_router ignoring message: %s" % message)
        return

    # Don't handle anything sent privately.
    if (message['channel_type'] == 'im'):
        # This response is not threaded and that is fine.
        say("Sorry, I don't respond to DMs.  Talk to me in a channel!")
        return

    if (message['channel'] not in cfg.SLACK_CHANNEL_WHITELIST_MAP):
        # #general / is_general is the roach motel of slack.
        # If it isn't whitelisted, just silently do nothing.
        channel_data = get_slack_channel_data(message['channel'])
        if (channel_data is not None and
            'is_general' in channel_data and
            channel_data['is_general']):
            return

        # Otherwise whine loudly.
        say("I'm sorry, but I'm only allowed to play in specific channels.\n" \
            "If you think I should be in this channel, please talk to a HoT and one of my handlers.\n" \
            "I will continue to respond with this message until I am removed from this channel.")
        return


    # Bridge bot mesages need to have the preamble sliced off.  Yuck.
    if bridge_bot_message:
        # This regex needs to avoid newlines for the first dot, and accept newlines
        # for the group, so we only use re.M, not re.DOTALL
        m = re.match(r"\*.+\*: ([\s\S]*)", message['text'], re.M)
        if m:
            message['text'] = m.group(1)
        else:
            raise Exception("Could not strip preamble from bridge bot message: %s" % message)

    # DEBUG
    # print("zdaemon_slack_router handling message: %s" % message)

    text = message['text']

    # Bang commands first.
    #
    # TODO: should these be case insensitive?
    if (re.search(r"^!ping($|\s)", text)):
        slack_ping(message)
    if (re.search(r"^!pinghelp($|\s)", text)):
        slack_pinghelp(message)
    if (re.search(r"^!zdhelp($|\s)", text)):
        slack_zdhelp(message)

    # TODO: Move these to their own "instance replacement" file,
    #       probably along with csa (from cube).  They don't really fit
    #       in cube or plusplus, and they are a bit too special case for generic triggers.
    if (re.search(r"^!gny($|\s)", text, flags=re.I)):
        slack_gny(message)
    if (re.search(r"^!rip($|\s)", text, flags=re.I)):
        slack_rip(message)

    triggers.slack_check_msg(message)
    cube.cubeSlackRouter(message)
    slack_plusplus_router(message)
