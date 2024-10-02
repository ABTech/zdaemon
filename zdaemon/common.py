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

from cachetools import cached, TTLCache
import logging
import re
import time
import unicodedata as ud

from slack_sdk.errors import SlackApiError

import config as cfg

# time of the call to init_common_config in seconds since epoch.
ZDAEMON_START_TIME = 0

# For Sending Messages
_ZULIP_CLIENT = None
_SLACK_CLIENT = None
_log = logging.getLogger("zdaemon-common")


def init_common_config(zulip_client, slack_client):
    global _ZULIP_CLIENT, _SLACK_CLIENT
    _ZULIP_CLIENT = zulip_client
    _SLACK_CLIENT = slack_client

    global ZDAEMON_START_TIME
    ZDAEMON_START_TIME = int(time.time())


def runzulip(handler):
    '''Runs the zulip listner loop.  Not expected to return.'''
    if _ZULIP_CLIENT is None:
        raise Exception("_ZULIP_CLIENT not configure in runzulip")

    _ZULIP_CLIENT.call_on_each_message(handler)


def sendpersonalz(who, msg):
    if _ZULIP_CLIENT is None:
        raise Exception("sendpersonalz: _ZULIP_CLIENT not configured")

    request = {
      "type": "private",
      "to": [who],
      "content": msg
    }
    response = _ZULIP_CLIENT.send_message(request)
    if (response["result"] == "success"):
        return True
    else:
        _log.error("sendpersonalz zulip error: " + response["msg"])
        return False


def sendz(zclass, instance, message, unfurl=False):
    ''' Send a zulip message to the given class and instance.

        The unfurl parameter is ignored.'''
    if _ZULIP_CLIENT is None:
        raise Exception("sendz: _ZULIP_CLIENT not configured")

    # Send a stream message
    request = {
      "type": "stream",
      "to": zclass,
      "topic": instance,
      "content": message
    }
    response = _ZULIP_CLIENT.send_message(request)
    if (response["result"] == "success"):
        return True
    else:
        _log.error("sendz zulip error: " + response["msg"])
        return False


def _sendsErrorCheck(res, channel_id, thread_ts, message):
    if res['ok'] != True:
        error_thread = '[NONE]'
        if thread_ts is not None:
            error_thread = thread_ts
        raise Exception('postMessage failure without Slack Api Exception: %s [%s,%s,%s]' % \
                        (res['error'], channel_id, message, error_thread))

    if 'message' not in res:
        raise Exception('post message response was ok but did not include message? [%s, %s]' % \
                        channel_id, message)


def sendsText(channel_id, message, thread_ts=None, unfurl=True):
    '''Send a raw text (not block) message to the specified slack
       channel id and (if supplied) thread.

       Remember, userids are used as channels for DMs, so there is no sendpersonalsText

       Returns the slack-canonicalized message object on success.
       Returns None on failure.
       Certain unusual failures will raise an exception.
    '''
    if _SLACK_CLIENT is None:
        raise Exception("_SLACK_CLIENT not configured")

    try:
        res = _SLACK_CLIENT.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            unfurl_links=unfurl,
            unfurl_media=unfurl,
            text=message)

        _sendsErrorCheck(res, channel_id, thread_ts, message)

        return res['message']
    except SlackApiError as e:
        _log.error("sendsText Slack Error: " + e.response["error"])
        return None


def sendsBlock(channel_id, message_blocks, fallback=None, thread_ts=None, unfurl=True):
    '''Sends a block message to the specific slack channel id and (if supplied) thread

        Remember, userids are used as channels for DMs, so there is no sendpersonalsBlock

       fallback is optional slack fallback text (suppresses a warning)
    '''
    if _SLACK_CLIENT is None:
        raise Exception("_SLACK_CLIENT not configured")

    try:
        res = _SLACK_CLIENT.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text=fallback,
            unfurl_links=unfurl,
            unfurl_media=unfurl,
            blocks=message_blocks)

        _sendsErrorCheck(res, channel_id, thread_ts, message_blocks)

        return res
    except SlackApiError as e:
        _log.error("sendsBlock Slack Error: " + e.response["error"])
        return None


def slackReact(message, emojiname):
    '''Applies the given reaction to the specified message.'''
    if _SLACK_CLIENT is None:
        raise Exception("_SLACK_CLIENT not configured")

    try:
        _SLACK_CLIENT.reactions_add(channel=message["channel"],
                                    name=emojiname,
                                    timestamp=message["ts"])
    except SlackApiError as e:
        _log.error("sendsBlock Slack Error: " + e.response["error"])


# Cache bot userid lookups for a day (if we really need to change bot ids, just kick zdaemon)
@cached(cache=TTLCache(maxsize=64, ttl=86400))
def get_slack_bot_userid(botid):
    if _SLACK_CLIENT is None:
        raise Exception("_SLACK_CLIENT not configured")

    res = _SLACK_CLIENT.bots_info(bot=botid)
    if not res['ok']:
        raise Exception("OK:False when fetching bot %s (result: %s)" % (botid, res))

    return res['bot']['user_id']


# Cache user lookups for an hour.
@cached(cache=TTLCache(maxsize=512, ttl=3600))
def get_slack_user_profile(userid):
    if _SLACK_CLIENT is None:
        raise Exception("_SLACK_CLIENT not configured")

    res = _SLACK_CLIENT.users_profile_get(user=userid)
    if not res['ok']:
        raise Exception("OK:False when fetching profile %s (result: %s)" % (userid, res))
    return res['profile']


def get_slack_user_email(userid, lhs_only=True):
    '''Returns the user's slack profile's email.  By default only returns the left hand side.

       Hard-codes the slack bridge bot to be "bridge-bot@ABTECH.ORG"
    '''
    if userid == cfg.SLACK_BRIDGE_BOT_ID:
        return "bridge-bot@ABTECH.ORG"

    profile_result = get_slack_user_profile(userid)

    if 'email' not in profile_result:
        raise Exception("no email in profile for %s, is it a bot?  do we have users:read.email scope?" % userid)

    email = profile_result['email']
    if lhs_only:
        email_split = email.split('@')
        return realID(email_split[0])
    else:
        return email


@cached(cache=TTLCache(maxsize=128, ttl=3600))
def get_slack_user_by_email(email):
    '''Returns the user object for the supplied email.  Returns None if not found, but raises an exception on other errors.'''
    if _SLACK_CLIENT is None:
        raise Exception("_SLACK_CLIENT not configured")

    user_result = _SLACK_CLIENT.users_lookupByEmail(email=email)
    if user_result['ok'] == 'false' and user_result['error'] == 'users_not_found':
        return None
    elif user_result['ok'] == 'false':
        raise Exception("user lookup failed in get_slack_user_by_email: %s" % user_result['error'])
    else:
        return user_result['user']


# TODO: If we need channel data more frequently, make channel caching a class so we
# can also precompute the forward and reverse maps.  A cache flush in this case
# would also need to be triggered by a miss in what is currently get_slack_channel_data.
#
# We cache this for a day since we aren't currently sensitive to changes in it after
# startup when we load the config.
#
# Dear Slack:  Your pagination system is ridiculous.  I should not
# need to cursor at all with a limit of 1000 to see 25 total visible channels.
@cached(cache=TTLCache(maxsize=1, ttl=86400))
def get_slack_channel_list():
    '''Returns full data of all channels we can see'''
    if _SLACK_CLIENT is None:
        raise Exception("_SLACK_CLIENT not configured")

    res = _SLACK_CLIENT.conversations_list(
        limit=1000,
        exclude_archived=True,
        types="public_channel,private_channel")
    if res['ok'] == 'false':
        raise Exception("get_slack_channel_list api call error: %s" % res['error'])
    channel_result = res['channels']

    while ('response_metadata' in res and
           'next_cursor' in res['response_metadata'] and
           res['response_metadata']['next_cursor'] != ""):
        cursor = res['response_metadata']['next_cursor']

        res = _SLACK_CLIENT.conversations_list(
            limit=1000,
            cursor=cursor,
            exclude_archived=True,
            types="public_channel,private_channel")
        if res['ok'] == 'false':
            raise Exception("get_slack_channel_list api cursor call error: %s" % res['error'])

        channel_result = channel_result + res['channels']

    return channel_result


def _validate_channel_data(c):
    '''Validate that a channel is usable in our maps.'''
    if 'name' not in c:
        # Skip any channel without a name, since we won't be able to map it anyway.
        return False
    if 'id' not in c:
        raise Exception("channel '%s' without an id in get_slack_channel_map ?" % c['name'])

    return True


def get_slack_channel_data(id):
    '''Return the channel object for the given channel id'''
    channels = get_slack_channel_list()
    for c in channels:
        if c['id'] == id:
            return c

    return None


def get_slack_channel_nametoid_map():
    '''Returns a map of channel name (without hash) -> channel id'''
    channels = get_slack_channel_list()
    res = {}
    for c in channels:
        if not _validate_channel_data(c):
            continue

        res[c['name']] = c['id']

    return res


def get_slack_channel_idtoname_map():
    '''Returns a map of channel id -> name (without hash)'''
    channels = get_slack_channel_list()
    res = {}
    for c in channels:
        if not _validate_channel_data(c):
            continue

        res[c['id']] = c['name']

    return res


def get_slack_message_permalink(channel, ts):
    '''Returns the permalink for the given message on the given channel.'''
    if _SLACK_CLIENT is None:
        raise Exception("_SLACK_CLIENT not configured")

    res = _SLACK_CLIENT.chat_getPermalink(channel=channel, message_ts=ts)

    if res['ok'] == 'false':
        raise Exception("get_slack_message_permalink api call error: %s" % res['error'])
    if 'permalink' not in res:
        raise Exception("get_slack_message_permalink: no permalink in ok result? %s" % res)

    return res['permalink']


def get_slack_thread(event):
    '''Return the parent thread of a message event.  No API calls involved.'''
    if (event['type'] != 'message'):
        raise Exception("get_slack_thread: not a message event (%s)" % event)

    if 'thread_ts' in event:
        return event['thread_ts']
    else:
        return event['ts']


def realID(id):
    """Converts the passed identifier into a more canonical form.
       Mostly deals with the same user across many realms.
    """
    name = id.rstrip()
    m = re.match(r'(\w+)@ABTECH.ORG', name)
    if m:
        name = m.group(1)
    else:
        m = re.match(r'(\w+)@ANDREW.CMU.EDU', name)
        if m:
            name = m.group(1)

    # If we don't have a realm at this point,
    # canonicalize to lowercase.
    if not re.match(r'(\w+)@(\w+)', name):
        name = name.lower()

    return name


def sendToMaintainer(message):
    ''' Sends a personal message to the maintainer(s) '''
    # TODO: Multiple Maintainers.

    if _ZULIP_CLIENT is None and _SLACK_CLIENT is None:
        raise Exception("no configured clients in sendToMaintainer?")

    if _ZULIP_CLIENT is not None:
        sendpersonalz(cfg.MAINTAINER, message)

    if _SLACK_CLIENT is not None:
        # TODO: There's an argument to be made that on slack there should just be a dedicated group
        # channel to log these errors to so multiple people can see it rather than getting DMs, but
        # just match zulip behavior for now.
        #
        # Never unfurl maintainer messages since we don't want to see random images from slack
        # message data.
        sendsText(cfg.SLACK_MAINTAINER, message, unfurl=False)


def is_maintainer(email):
    ''' Returns true if the provided fully qualified email address is
        a maintainer
    '''
    # TODO: Multiple Maintainers.
    return email == cfg.MAINTAINER


def hasRTLCharacters(thing):
    ''' Returns True if there are RTL characters inside of the thing. '''
    for c in list(thing):
        # see https://stackoverflow.com/a/75739782/3399890
        # Basically if we see any strong RTL character or the RTL control characters
        # assume we need to isolate it.
        if ud.bidirectional(c) in ['R', 'AL', 'RLE', 'RLI']:
            return True

    return False
