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

from datetime import datetime

import functools
import pytz
from random import randrange
import re
import sqlite3
import time
import unicodedata as ud

import config as cfg
from common import sendz
from common import slack_active, sendsText, get_slack_thread, get_slack_user_email
from common import realID, hasRTLCharacters

# sqlite Schema
#
# CREATE TABLE ppdata (thing TEXT PRIMARY KEY NOT NULL,
#                      score INTEGER NOT NULL);
# CREATE TABLE lastpp (username TEXT NOT NULL,
#                      thing TEXT NOT NULL,
#                      direction INTEGER NOT NULL,
#                      timestamp INTEGER NOT NULL,
#              PRIMARY KEY(username, thing, direction));


_PP_SQLITE_FILE = None
_PP_QUERY_INSTANCE = "plusplus.query"
_PIT_TIMEZONE = pytz.timezone('America/New_York')

def init_pp_config(zdaemon_data_dir):
    global _PP_SQLITE_FILE

    _PP_SQLITE_FILE = zdaemon_data_dir + "/ppdata.sqlite"


def _getDBHandle():
    ''' Get a DB handle, just the way we like it. '''
    if _PP_SQLITE_FILE is None:
        raise Exception("need to call init_pp_config first")

    dbh = sqlite3.connect(_PP_SQLITE_FILE)
    dbh.row_factory = sqlite3.Row

    # If legacy badly encoded plusplus entries are allowed
    # into the database, you will need to enable this.
    #
    # dbh.text_factory = lambda b: b.decode(errors = 'ignore')

    return dbh


def getPlusplusStats():
    '''Returns a dict with a 'count' and 'sum' field that
       describes the plusplus database.
    '''
    dbh = _getDBHandle()
    try:
        rows = dbh.execute("SELECT COUNT(*) AS count, SUM(score) AS sum FROM ppdata;").fetchall()
        if (len(rows) != 1):
            raise Exception("getPlusplusStats not exactly 1 row")
        else:
            return rows[0]
    finally:
        dbh.close()


def _renderPlusplusResultLine(thing, value):
    '''Renders a plusplus result, accounting the fact that there may be RTL characters.
       If there _are_ rtl characters in the thing, then it will force the whole
       string LTR, the thing to be its first strong character, and the colon on to
       again be LTR.
    '''
    if hasRTLCharacters(thing):
        return "\u2066\u2068%s\u2069\u2066: %d\u2069\u2069\n" % (thing, value)
    else:
        return "%s: %d\n" % (thing, value)


def doPlusplusQuery(message, reply):
    '''Handle plusplus.query lookups.

       reply(message) sends the reply to the right place.
    '''
    # TODO: Do we really need full regexp support?  Doing that
    # requires us to read the entire database each time.  Thankfully
    # we can cursor through it instead of a fetchall(), but it'd
    # be better if sqlite could just do the lift for us -- either
    # with the REGEXP extension or just allow only SQL LIKE queries.
    m = re.search(r'([-]?)\{(.+)\}', message)
    if not m:
        reply("You need to wrap query parameters in { }, as in:\n" \
              "{zdaemon}")
        return

    sort_direction = "DESC"
    if m.group(1) == '-':
       sort_direction = "ASC"
    pattern = re.compile(m.group(2))

    results = []
    dbh = _getDBHandle()
    try:
        stmt = "SELECT thing, score FROM ppdata ORDER BY score %s;" % sort_direction
        cur = dbh.execute(stmt)

        BATCH_SIZE = 500
        while True:
            rows = cur.fetchmany(BATCH_SIZE)
            if not rows:
               break
            for row in rows:
                if pattern.search(row['thing']):
                   results.append(row)
    finally:
        dbh.close()

    msg = "Things matching /%s/:\n" % pattern.pattern
    for row in results:
        msg += _renderPlusplusResultLine(row['thing'], row['score'])
    reply(msg)


def _ppquery(cursor, thing):
    '''Does a single lookup using the provided cursor for thing.
       Returns its score if it exists, and None otherwise.
    '''
    rows = cursor.execute(
        "SELECT score FROM ppdata WHERE thing=:thing",
        {"thing": thing}).fetchall()

    if (len(rows) == 0):
        return None
    elif (len(rows) > 1):
        raise Exception("ppquery: %s has more than one row?" % thing)
    else:
        return int(rows[0]['score'])


def _lastpptime_query(cursor, id, thing, inc):
    '''Returns the last time that id modified thing
       with an inc operation.

       inc is 1 or -1

       Uses the provided cursor.

       Returns 0 (e.g. the epoch) if no entry found.
    '''
    if inc != 1 and inc != -1:
        raise Exception("_lastpptime_query: bad increment %s" % inc)

    rows = cursor.execute(
        """SELECT timestamp FROM lastpp
                WHERE username=:id AND direction=:inc AND thing=:thing""",
                {"id": id, "inc": inc, "thing": thing}).fetchall()

    if (len(rows) == 0):
        return 0
    elif (len(rows) > 1):
        raise Exception("_lastpptimequery multiple rows for %s, %d, %s"
                        % (id, inc, thing))
    else:
        return rows[0]['timestamp']


def _plusplus(cursor, sender, display_sender,
              inc, thing, reply):
    '''Does a single ++ or -- operation using the provided cursor.

       cursor is the DB cursor
       sender is the unqualified sender (e.g. email LHS)
       display_sender is the string to use in message responses for the sender
                      (except when as a target for a plusplus)
       inc is 1 or -1
       thing is the thing
       Error or humor replies are sent via reply(message)

       returns new value or None if nothing changed.
    '''
    if inc != 1 and inc != -1:
        raise Exception("_plusplus: bad increment %s" % inc)

    # Legacy zdaemon made this call, but realID() didn't actually have correct regexes, so
    # it never worked!  The behavior is surprising when it is working, as plusplus will report
    # the score of the canonical id, but display the name of the noncanonical ID.  e.g.
    # "zdaemon@ABTECH.ORG: 1023482" while recording & reporting the score for "zdaemon"
    #
    # We think users plusplusing an email address, even an andrew or abtech one, expect that
    # the string for the email itself is the thing that gets changed.
    # thing_id = realID(thing)
    thing_id = thing

    # Self Plusplus Penalty
    self_pp_penalty = False
    if (thing_id == sender and inc == 1):
        reply("Whoa, @bold(loser) trying to plusplus themselves.\n" \
              "Changing to %s--" % thing_id)
        inc = -1
        self_pp_penalty = True

    # This is zdaemon's show
    if (thing_id == 'zdaemon'):
        if (inc == -1):
            reply("Are YOU disrespecting me, %s? Huh? Are you?\n" \
                  "I think you are!" % display_sender)
            return None
        elif (inc == 1):
            reply("Oooh. I just love it when you do that! :)\n\n" \
                  "What are you doing later, %s?" % display_sender)

    # Don't talk about her age either.
    if (thing_id == 'zdaemon.age'):
        reply("It's impolite to talk about a daemon's age.\n" \
              "How do you like it, %s?" % display_sender)
        thing = '%s.age' % sender
        reply("%s++" % thing)
        res = _plusplus(cursor, 'zdaemon', 'zdaemon', 1,
                        thing, reply)
        if res is not None:
            # Multiple pp's in a row will fail, act cool.
            reply('%s: %d' % (thing, res))
        return None

    # Hitting zdaemon is rude
    if (thing_id == 'zdaemon.whap'):
        if (inc == 1):
            msg = "Hey, that hurt, %s!" % display_sender
            if (randrange(100) >= 50):
                msg = "You'd better watch out, %s." % display_sender
            reply(msg)
        elif (inc == -1):
            if (randrange(100) >= 50):
                reply("Thank you, %s. You will be spared..." % display_sender)

    last_action_time = _lastpptime_query(cursor, sender, thing_id, inc)
    allowed_at_seconds = last_action_time + 3600
    nowtime = int(time.time())

    # XXX Election Edition - disable this check
    if (allowed_at_seconds > nowtime and not self_pp_penalty):
        # 60 Minute Rule
        allowed_at = datetime.fromtimestamp(allowed_at_seconds, _PIT_TIMEZONE)
        allowed_at_str = allowed_at.strftime("%H:%M:%S")
        reply("@bold(Not) changing %s (60min rule until %s) for %s." \
              % (thing_id, allowed_at_str, sender))
        return None

    cursor.execute("""
        INSERT OR REPLACE INTO ppdata (thing, score)
            VALUES (:thing,
                   COALESCE((SELECT score + :inc FROM ppdata WHERE thing=:thing),:inc));""",
                   {"thing": thing_id, "inc": inc})
    ret = _ppquery(cursor, thing_id)
    if ret is None:
        raise Exception("_plusplus: %s doesn't exist after I just changed it?" % thing_id)

    # Update lastpp time
    cursor.execute("""INSERT OR REPLACE
                         INTO lastpp(username,
                                     thing,
                                     direction,
                                     timestamp)
                    VALUES (:sender, :thing, :inc, :timestamp);""",
                    {"sender": sender,
                     "thing": thing_id,
                     "inc": inc,
                     "timestamp": nowtime})

    return ret


_SLACK_CHANNEL_PATTERN = re.compile(r'<#([a-z0-9]+)(|.*)?>')
_SLACK_USER_PATTERN = re.compile(r'<@([a-z0-9]+)(|.*)?>')

def _ppSlackEntityFilter(thing, reply):
    '''Attempts to resolve plusplus targets that are slack entities into better targets.

        thing is the plusplus target to examine
        reply is used to send error messages if the thing is disallowed.

        returns canonical name (might be the same) or None if this should be disallowed.
    '''
    # Note that this code still runs on zulip, but it probably is an error there as well.

    # First, we don't support channel entities at all.  So, if there's a channel entity
    # anywhere within the thing, abort now.
    m = _SLACK_CHANNEL_PATTERN.search(thing)
    if m:
        entity = m.group(1)
        reply("It looks like you are trying to plusplus something that contains the "
              "slack channel %s but this is not supported.  "
              "Consider omitting the hash mark." % entity.upper())
        return None

    # Detect someone plusplussing a slack user, and convert it to an andrew id or forbid.
    # This tries to get embedded users, but it has limits, especially in unusual circumstances
    # like, for example foo<@zdaemon|zdaemon>bar, where we'll replace the thing in the middle,
    # but it probably is gibberish.  Then again, it is sort of gibberish anyway, so...
    while True:
        m = _SLACK_USER_PATTERN.search(thing)
        if not m:
            # Nothing left in the pattern that matches an embedded user.
            break

        could_not_replace_slack_entity = True
        entity = m.group(1)

        hint = "  If this is a user, please use their andrew id."

        try:
            if slack_active():
                # Best effort attempt to convert this to an andrew id.
                email = get_slack_user_email(entity, False)

                # Only used if we fail to canonicalize.
                hint += "  When I looked it up, I got %s which didn't look like an andrew account." % email

                # Remember that everyone gets trapped with using regexes for email addresses.
                # Luckily the ones we care about here are not plus addressed and don't use the
                # full rfc*822 formatting, since we're specifally looking for the andrew ID.
                if re.fullmatch(r'([\-\.\w]+)@([\.\w]+)', email):
                    new_thing = realID(email)

                    # Check if it still looks like an email address.
                    if not re.fullmatch(r'([\-\.\w]+)@([\.\w]+)', new_thing):
                        # It canonicalized successfully, do a replacement.
                        # (Use the original match object to replace it)
                        thing = thing[:m.start()] + new_thing + thing[m.end():]
                        could_not_replace_slack_entity = False
        except:
            # If an exception happens, proceed almost as if it wasn't valid anyway.
            hint += "  I tried to look it up, but I wasn't able to."
            pass

        # If we haven't turned this into something usable, abort!
        if could_not_replace_slack_entity:
            what_string = "the slack entity"
            if m.start() != 0:
                what_string = "something containing the slack entity"
            reply("It looks like you might be trying to plusplus %s: %s, "
                  "but this is not supported.%s" % (what_string, entity.upper(), hint))
            return None

    return thing


def _ppSlackEmailFilter(thing):
    '''
        Slack always turns emails into markdown, we need to strip that out before using it for plusplus.
    '''
    if not slack_active():
        return thing

    # These look like "<mailto:zdaemon@abtech.org|zdaemon@abtech.org>"
    #
    # We're knowingly walking into the trap of generating a regex for an email address.
    # It is an imperfect world.  Be nice.
    m = re.fullmatch(r'<mailto:([\-\.\+\w]+@[\.\w]+)\|\1>', thing)
    if m:
        thing = m.group(1)

    return thing


_PLUSPLUS_THING_PATTERN = re.compile(r'([^\s]{2,})(\+\+|--|\~\~)[\!\:\;\?\.\,\)\]\}\s]+([\w\W]*)')

def scanPlusPlus(sender, message, reply, display_sender=None):
    #log("In scanplusplus: %s" % message)

    if display_sender is None:
        display_sender = sender

    results = {}
    haystack = message

    if (re.search(r'(\+\+|--|\~\~)$', haystack)):
        # For the pattern we use below to work, we can't have
        # an op as the very end of the string.  So append a dot.
        haystack += "."

    dbh = _getDBHandle()
    cur = dbh.cursor()
    cur.execute("BEGIN")
    try:
        m = _PLUSPLUS_THING_PATTERN.search(haystack)
        while m is not None:
            haystack = m.group(3)
            thing = m.group(1).lower()
            op = m.group(2)

            # print ("%s / %s" % (thing, op))

            # Set up next loop.  Done before we decide if we need to slack-entity-filter
            # this item for readability reasons.
            #
            # Don't reference m inside the loop after this point!
            m = _PLUSPLUS_THING_PATTERN.search(haystack)

            # Forbid #channels and @users that we can't convert to an andrew account.
            thing = _ppSlackEntityFilter(thing, reply)
            if thing is None:
                # Need to filter it!
                continue

            # Strip slack email address markdown.
            thing = _ppSlackEmailFilter(thing)

            if (thing == "year"):
                results['year'] = datetime.now(_PIT_TIMEZONE).year
            elif (thing == "month"):
                results['month'] = datetime.now(_PIT_TIMEZONE).month
            elif (thing == "day"):
                results["day"] = datetime.now(_PIT_TIMEZONE).day
            elif (thing == "hour"):
                results["hour"] = datetime.now(_PIT_TIMEZONE).hour
            elif (thing == "minute"):
                results["minute"] = datetime.now(_PIT_TIMEZONE).minute
            elif (thing == "second"):
                results["second"] = datetime.now(_PIT_TIMEZONE).second
            elif (thing == "life"):
                results["life"] = 0
            elif (thing == "18290"):
                results["18290"] = 290
            else:
                res = None
                if (op == "~~"):
                    res = _ppquery(cur, thing)
                elif (op == "++"):
                    res = _plusplus(cur, sender, display_sender,
                                    1, thing, reply)
                elif (op == "--"):
                    res = _plusplus(cur, sender, display_sender,
                                    -1, thing, reply)

                if (res is not None):
                    results[thing] = res

        cur.execute("COMMIT")
    except Exception as e:
        cur.execute("ROLLBACK")
        raise e
    finally:
        dbh.close()

    if (len(results.keys()) > 0):
        # Nonzero results, so we respond.
        msg = ""
        for k in results.keys():
            msg += _renderPlusplusResultLine(k, results[k])
        reply(msg)


def checkPP(zclass, instance, sender, message):
  if (zclass == cfg.ZDAEMON_CLASS and instance == _PP_QUERY_INSTANCE):
    sendResponse = functools.partial(sendz, cfg.ZDAEMON_CLASS, _PP_QUERY_INSTANCE)
    doPlusplusQuery(message, sendResponse)

  if (zclass == cfg.ZDAEMON_CLASS or
      zclass == cfg.ABTECH_CLASS or
      zclass == cfg.GHOSTS_CLASS):
    # Determine where our replys will go.
    reply_class = cfg.ZDAEMON_CLASS
    if zclass == cfg.GHOSTS_CLASS:
       reply_class = cfg.GHOSTS_CLASS

    sendResponse = functools.partial(sendz, reply_class, "plusplus")
    scanPlusPlus(sender, message, sendResponse)


def slack_plusplus_router(message):
    '''Process the given message event for plusplus responses.'''
    sender = get_slack_user_email(message['user'])
    display_sender = "<@%s>" % message['user']
    thread = get_slack_thread(message)
    sendResponse = functools.partial(sendsText, message['channel'], thread_ts=thread)

    text = message['text']
    if(re.search(r"^!(ppquery|plusplusquery)($|\s)", text)):
        doPlusplusQuery(message['text'], sendResponse)

    scanPlusPlus(sender, message['text'], sendResponse,
                 display_sender=display_sender)
