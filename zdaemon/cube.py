# Cube handling library.
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

import functools
import json
import os
import re
import sqlite3
import subprocess
import time

from random import randrange

import config as cfg
from common import is_maintainer, realID, sendz
from common import sendsBlock, sendsText
from common import get_slack_thread, get_slack_user_email
from common import get_slack_message_permalink, get_slack_channel_data

# sqlite Schema
#
# CREATE TABLE CUBES (ID INT PRIMARY  KEY	NOT NULL,
# 			          SUCKS		INT	NOT NULL,
#			          SLURP_DATE	DATE	NOT NULL,
#			          SLURP_BY	CHAR(100) NOT NULL);
# CREATE TABLE LASTSUCKS (username TEXT NOT NULL,
#                         cube INT NOT NULL,
#                         direction INT NOT NULL,
#                         timestamp INT NOT NULL,
#              PRIMARY KEY(username, cube, direction));

# These must be initialized by calling init_cube_config
# before this module will work properly.
CUBEDIR = None
_CUBE_LOG_FILE = None
_LAST_CUBE_JSON_FILE = None # Metadata about most recent cube.
_CUBE_SQLITE_FILE = None

def init_cube_config(zdaemon_data_dir):
    global CUBEDIR
    global _CUBE_LOG_FILE
    global _CUBE_SQLITE_FILE
    global _LAST_CUBE_JSON_FILE

    CUBEDIR = zdaemon_data_dir + "/cubes"

    _CUBE_LOG_FILE = zdaemon_data_dir + "/cube.log"
    _CUBE_SQLITE_FILE = zdaemon_data_dir + "/cube.sqlite"
    _LAST_CUBE_JSON_FILE = zdaemon_data_dir + "/cube.last.json"


def _getCountWithCursor(cur):
    '''Returns the number of cubes, using the provided cursor.

       Useful for transactions.
    '''
    res = cur.execute("SELECT MAX(id) AS count FROM cubes;").fetchall()
    if (len(res) != 1):
        raise Exception("Not exactly one row in cube _getCountWithCursor?")

    return int(res[0]['count'])


def getCount():
    ''' Returns the number of cubes (also, the number for the highest numbered cube)'''
    dbh = _getDBHandle()
    try:
        res = _getCountWithCursor(dbh.cursor())
    finally:
        dbh.close()
    return res


def getLastCubeMetadata():
    '''Returns stored metadata about the last sent cube
       (what data is available depends on what backend is in use)
    '''
    if _LAST_CUBE_JSON_FILE is None:
        raise Exception("need to call init_cube_config")

    try:
        with open(_LAST_CUBE_JSON_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        # If we don't have a _LAST_CUBE_JSON_FILE, then we make something
        # up and hopefully a cube will eventually be sent to recreate the file.
        #
        # Obviously this will cause some trouble if we don't even have
        # 1 cube yet, but this is not the only thing that will cause
        # trouble in that case.
        return { 'cube_num': 1, 'scorable': False }


def getCubeContent(cube_num):
    ''' Returns the cube content for the provided cube number
        Will through file-related exceptions if the file doesn't exist'''
    if CUBEDIR is None:
        raise Exception("need to call init_cube_config")

    with open(("%s/cube.%d" % (CUBEDIR, cube_num)), "r", errors='replace') as f:
        return f.read()


def _getDBHandle():
    ''' Get a DB handle, just the way we like it. '''
    if _CUBE_SQLITE_FILE is None:
        raise Exception("need to call init_cube_config")

    dbh = sqlite3.connect(_CUBE_SQLITE_FILE)
    dbh.row_factory = sqlite3.Row

    return dbh


class SendableCube:
    '''Object for a cube that can be sent.

       Construct with a number to select a specific cube or -1 for random
       This will enforce sucks score rate limiting on random pulls.

       Members:
         cube_num: ID of cube
         score: Sucks/Rocks score
         slurp_date: Slurp date as seconds since epoch
         slurp_date_string: Slurp date as a human readable string
         cube_text: Text of the cube
         _tracking_done: Tracks if we have logged the cube and
                         updated _LAST_CUBE_JSON_FILE or not.
    '''
    def __init__(self, cube_num = -1, scorable=False):
        '''Loads the specified (or a random) cube into class members.

           If scorable is false, we will mark nosucks at the time the cube is sent.
        '''
        dbh =  _getDBHandle()
        cubelist = []

        try:
            res = dbh.cursor()
            if cube_num == -1:
                # Random cube.
                # Make sure we filter by a sucks score (0->11)
                suck_limit = randrange(12)
                stmt = """SELECT id, sucks, slurp_date,
                                 datetime(slurp_date, 'unixepoch') as SLURP_DATE_STRING
                            FROM cubes
                            WHERE sucks <= :suck_limit
                            ORDER BY RANDOM()
                            LIMIT 1;"""
                res.execute(stmt, {"suck_limit": suck_limit})
            else:
                stmt = """SELECT id, sucks, slurp_date,
                                 datetime(slurp_date, 'unixepoch') as SLURP_DATE_STRING
                            FROM cubes WHERE id=:id;"""
                res = dbh.execute(stmt, {"id": cube_num})

            cubelist = res.fetchall()
        finally:
            dbh.close()

        if (len(cubelist) != 1):
            raise Exception("SendableCube: %d results for lookup of %d" % (len(cubelist), cube_num))
        row = cubelist[0]

        self.cube_num = row['id']  # needed for random cubes
        self.score = row['sucks']
        self.slurp_date = row['slurp_date']
        self.slurp_date_string = row['slurp_date_string']
        self.cube_text = getCubeContent(self.cube_num)
        self.scorable = scorable

        self._tracking_done = False


    def _trackCube(self, metadata={}):
        if (self._tracking_done):
            # If we get called twice, that's an error.
            raise Exception("_trackCube called twice for same cube: %d" % self.cube_num)
        else:
            # Update cube log
            # TODO: Why do we bother to store the slurp date here?
            with open(_CUBE_LOG_FILE, "a") as f:
                f.write("%d:%d\n" % (self.cube_num, self.slurp_date))

            # Update metadata json
            metadata['cube_num'] = self.cube_num
            metadata['scorable'] = self.scorable
            with open(_LAST_CUBE_JSON_FILE, "w") as f:
                f.write(json.dumps(metadata))

            self._tracking_done = True


    def sendZulip(self):
        '''Sends this cube to zulip and tracks it.'''
        # Prepare Message to Send
        msg = self.cube_text
        msg += "\n\n"
        msg += "| |%s %d(%d)|\n" % (self.slurp_date_string, self.cube_num, self.score)
        msg += "| --- | ---:|" # needed because zulip tables need 2 rows

        # Send Cube
        sendz(cfg.ZDAEMON_CLASS, 'cube', msg)

        # Track it!
        self._trackCube()


    def _getSlackBlocks(self):
        '''Returns the blocks necessary for a slack message.'''
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": self.cube_text
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                      "type": "mrkdwn",
                      "text": "_<!date^%d^{date_num} {time_secs}|%s (UTC)> %d(%d)_" % \
                          (self.slurp_date, self.slurp_date_string, self.cube_num, self.score)
                    }
                ]
            }
        ]


    def sendSlack(self, channel=None, thread_ts=None):
        '''Sends this cube to the cubes channel & tracks it.'''
        if _LAST_CUBE_JSON_FILE is None:
            raise Exception("need to call init_cube_config")

        if channel is None:
            channel = cfg.SLACK_CUBE_CHANNEL_ID

        res = sendsBlock(channel, self._getSlackBlocks(),
                         fallback=self.cube_text,
                         thread_ts=thread_ts)

        metadata = {}
        if res is not None:
            metadata['ts'] = res['ts']  # the message
            metadata['thread_ts'] = get_slack_thread(res['message'])  # the message or its parent
            metadata['channel'] = res['channel']
            metadata['permalink'] = get_slack_message_permalink(res['channel'], res['ts'])
        else:
            raise Exception("sendsBlock no message in SendableCube.sendSlack?")

        if not self.scorable:
            last_cube = getLastCubeMetadata()
            if last_cube['scorable']:
                # If we are not scorable, but the last cube was, we need to announce
                # that voting has closed early, and point at the new cube.
                if('channel' in last_cube and 'ts' in last_cube):
                    msg = "Voting closed early due to gimme'd cube.  See <%s|here>." \
                        % metadata['permalink']
                    sendsText(last_cube['channel'], msg,
                              thread_ts=last_cube['thread_ts'])
                else:
                    # TODO log an error somewhere, but don't raise an exception,
                    # this is noncritical.
                    pass

        self._trackCube(metadata)


def sendCube(cube_to_send = -1):
    ''' Sends A Cube to Zulip.  Deprecated.

        Pass a number to select or -1 for random
        Will enforce sucks score rate on random pulls.
    '''
    SendableCube(cube_to_send).sendZulip()


def cubeService(sendReply):
    ''' Return the paths of particular files.
        Not really sure why this is a public interface.
    '''
    if _CUBE_SQLITE_FILE is None:
        raise Exception("need to call init_cube_config")

    msg = "Cube count: [deprecated]\n" \
          "Cube db (gdbm): [deprecated]\n" \
          "Cube sqlite: %s\n" \
          "Cube log: %s\n" \
          "Last cube: [deprecated]\n" \
          "Last cube JSON: %s\n" \
          "Nosucks file: [deprecated]\n" \
          "Lockfile: [deprecated]\n" \
          % (_CUBE_SQLITE_FILE,
             _CUBE_LOG_FILE,
             _LAST_CUBE_JSON_FILE)

    sendReply(msg)


def _cubeSucks(op, last_cube, sender, reply):
    '''Performs a sucks or rocks operation.
       op: 1 or -1 (-1 is "rocks").  Other values will raise an exception.

       Note: Will respond on cube.sucks and cube.rocks respectively.

       reply(message) is a function that will respond to the correct place.
    '''
    if _CUBE_SQLITE_FILE is None:
        raise Exception("need to call init_cube_config")

    op_text_name = "unknown"
    if op == -1:
        op_text_name = "rocks"
    elif op == 1:
        op_text_name = "sucks"
    else:
        raise Exception("bad cubeSucks op: %s" % op)

    nowtime = int(time.time())

    ## NOTE:
    # Using SQLITE for the lastsucks tracking is _NEW_
    # in the python zdaemon, previously this used a
    # flat file and shelling out to grep.
    #
    # The new implementation also doesn't bother keeping
    # obsolete events around or recording events to
    # a sucks.debug log -- we only track the most
    # recent event for any (user, direction, cube) triplet.

    dbh = _getDBHandle()
    stmt = """SELECT timestamp FROM lastsucks
                WHERE username=:sender AND
                      direction=:op AND
                      cube=:cube;"""
    rows = dbh.execute(stmt, {"sender": sender,
                              "op": op,
                              "cube": last_cube}).fetchall()

    if (len(rows) > 1):
        dbh.close()
        raise Exception("cubeSucks: %d rows for (%s, %d, %s)" % sender, op, last_cube)
    elif (len(rows) == 1):
        # Was it more than an hour ago?
        lastsucks = rows[0]['timestamp']

        # XXX Election edition (skip this check)
        if (nowtime - lastsucks < 3600):
            dbh.close()
            reply("Bad mojo %s, not updating the sucks db.  Please wait 1 hour." % sender)
            return

    # OK, We're good to proceed.  Execute the operation.
    # Use a transaction to get both tables in one go.
    new_score = 0
    cur = dbh.cursor()
    cur.execute("BEGIN")
    try:
        cur.execute("UPDATE cubes SET sucks=sucks + :op WHERE ID = :id;",
                    {"op": op, "id": last_cube})
        cur.execute("""INSERT OR REPLACE
                         INTO lastsucks(username,
                                        direction,
                                        cube,
                                        timestamp)
                    VALUES (:sender, :op, :cube, :timestamp);""",
                    {"sender": sender,
                     "op": op,
                     "cube": last_cube,
                     "timestamp": nowtime})

        # Update done, get the new score.
        cur.execute("SELECT sucks FROM cubes WHERE ID=:id",
                    {"id": last_cube})
        rows = cur.fetchall()
        if (len(rows) != 1):
            raise Exception(
                "cubeSucks error getting new score for cube %d" % last_cube)
        else:
            new_score = rows[0]['sucks']

        cur.execute("COMMIT")
    except Exception as e:
        cur.execute("ROLLBACK")
        raise e
    finally:
        dbh.close()

    msg = "Okay, I recorded %s's %s vote. Cube #%d currently has %d sucks votes.\n\n" \
          "(The higher its score the less likely it is to be chosen, >11 = ignore)" \
          % (sender, op_text_name, last_cube, new_score)

    reply(msg)


def cubeSucksZulip(op, sender, reply):
    metadata = getLastCubeMetadata()
    # Preprocess the scorable check since the message is
    # dependent on the backend.
    #
    # XXX Election Edition (skip this check)
    if (not metadata['scorable']):
        reply("4 hits with a wet noodle %s, you cannot change sucks votes after a cube.gimme." % sender)
        return

    _cubeSucks(op, metadata['cube_num'], sender, reply)


# TODO: On slack it is plausible that we could not need
# to close voting following a gimme, since the confusion
# of all-cubes-to-one-instance doesn't exist on slack,
# and it is always clear that a gimme triggered a cube,
# as it will be threaded with the command.
#
# This would mean a change to "last cube" handling,
# possibly (but not necessarily) across the board
# (including for cube.info)
def cubeSucksSlack(op, message, reply):
    '''Slack preprocessing of cube sucks/rocks'''
    # We don't need the actual message content here, but we do
    # need its metadata.
    #
    # Effectively, this prefilters the use of sucks/rocks on slack
    # to only be in valid places.
    #
    # Rules:
    #   Sucks/Rocks is _only_ allowed on the cubes channel
    #   Sucks/Rocks is _only_ allowed at the top level of the channel
    #     or in the thread of the most recently scorable cube.
    if cfg.SLACK_CUBE_CHANNEL_ID == "":
        raise Exception("Empty SLACK_CUBE_CHANNEL_ID in cubeSucksSlack.  Check config.")

    cube_channel_name = '<#%s>' % cfg.SLACK_CUBE_CHANNEL_ID

    # Allowable channel?
    if message['channel'].upper() != cfg.SLACK_CUBE_CHANNEL_ID:
        reply("Sorry, I can only process sucks/rocks votes on the %s channel." % cube_channel_name)
        return

    # Allowable thread?
    last_cube = getLastCubeMetadata()
    if 'thread_ts' in message:
        if (last_cube['channel'].upper() != cfg.SLACK_CUBE_CHANNEL_ID or
            last_cube['thread_ts'] != message['thread_ts']):
            permalink_string = 'most recent votable cube'
            if 'permalink' in last_cube:
                # if we have a link, use it
                permalink_string = '<%s|%s>' % (last_cube['permalink'], permalink_string)
            reply("Sorry, you can only issue a vote directly in the %s channel itself " \
                  "or in the thread of the %s." % \
                    (cube_channel_name, permalink_string))
            return

    # At this point we are being commanded in the cubes channel
    # or in the thread of the most recent cube on that channel.

    # Check if the most recent cube was scorable.
    #
    # This also allows us to use an embedded slack name in the response.
    # (since we use the email LHS as the db key)
    #
    # XXX Election Edition (skip this check)
    if not last_cube['scorable']:
        last_cube_str = "most recent cube"
        if 'permalink' in last_cube:
            last_cube_str = "<%s|%s>" % (last_cube['permalink'], last_cube_str)

        reply("4 hits with a wet noodle <@%s>, no cubes are currently open for votes.\n\n" \
              "(Was the %s a gimme?)" % (message['user'], last_cube_str))
        return

    # TODO: It is possible that we should actually send any reply
    # to the cube thread _also_, but that starts to get noisy and messy
    # so we will wait to see what user response looks like.
    sender = get_slack_user_email(message['user'])
    _cubeSucks(op, last_cube['cube_num'], sender, reply)


def _processCubeGimme(message):
    '''Returns a tuple of (SendableCube, cube_num) based or None if a number was provide that could not be found.
       If mesage does not contain a number, or is None, returns a random cube (cube_num is left to None in this case)

       Tags nosucks file if needed.
    '''
    cube_num = None
    m = re.search(r'(-?\d+)', message)
    if m:
        cube_num = int(m.group(1))

    if cube_num is not None:
        max_cube = getCount()
        if (cube_num <= 0 or cube_num > max_cube):
            return (None, cube_num)
        else:
            return (SendableCube(cube_num), cube_num)
    else:
        return (SendableCube(), None)


def cubeGimmeZulip(sender, message):
    '''Zulip Processing of message to cube.gimme'''
    (cube, cube_num) = _processCubeGimme(message)

    if cube is not None:
        cube.sendZulip()
    else:
        # Shouldn't be reachable if cube_num is not None
        sendz(cfg.ZDAEMON_CLASS, "cube",
              "Well, %s, you must be on crack because I can't find cube %d." \
	           % (sender, cube_num))


def cubeGimmeSlack(message):
    '''Slack Procesing of !cubegimme (num)'''
    (cube, cube_num) = _processCubeGimme(message['text'])
    if cube is not None:
        cube.sendSlack(channel=message['channel'], thread_ts=get_slack_thread(message))
    else:
        sendsText(message['channel'], "Well, <@%s>, you must be on crack because I can't find cube %d." \
            % (message['user'], cube_num),
            thread_ts=get_slack_thread(message))

    # TODO: We also need to go find the most recently legitimately sent cube (if any) and update its thread
    # with a note that voting has closed, and a link to the message we just sent.


def cubeInfo(message, sendReply, slack_channel=None):
    '''Handles cube.info, optionally taking a numbered cube insteaed
       of processing the most recent cube.

       message is the full text of the input message (we use the first number if there)
       sendReply(msg) sends a reply to the correct place.
       slack_channel indicates our current channel for the request, on slack only, to help
                     protect private channels.
    '''
    cube_num = -1
    last_cube_metadata = {}
    m = re.search(r'(-?\d+)', message)
    cube_text_preface = "The wisdom contained within:"

    if m:
        cube_num = int(m.group(1))
    else:
        # We are being asked for info about the most recent cube.
        last_cube_metadata = getLastCubeMetadata()

        # If there is a 'channel' component of the metadata, and we have a slack_channel,
        # we need to see if the last send was private.  If it is, we only respond
        # successfully if this is the _same_ channel.  Otherwise, we can at best send
        # a link to it and suggest the user retry.
        if (slack_channel is not None and
            'channel' in last_cube_metadata and
            last_cube_metadata['channel'] != slack_channel):
            last_cube_channel = get_slack_channel_data(last_cube_metadata['channel'])
            if last_cube_channel['is_private']:
                private_channel_text = 'private channel'
                if 'permalink' in last_cube_metadata:
                    # In this case, we are willing to unfurl the permalink to anyone who can
                    # see it.
                    private_channel_text = "<%s|%s>" % (last_cube_metadata['permalink'],
                                                        private_channel_text)
                sendReply(
                    "Sorry, the most recent cube was sent to a %s.\n"
                    "You would need to retry this request there." % private_channel_text)
                return

        cube_num = last_cube_metadata['cube_num']

        missed_it_string = "missed it"
        if 'permalink' in last_cube_metadata:
            # If we have a link, might as well show it.  We tell it not to unfurl
            # when the message is sent to avoid duplication though.
            #
            # (note: This is a slack mrkdwn format link, but
            #  zulip doesn't store permalinks anyway so it will never see this)
            missed_it_string = "<%s|%s>" % (last_cube_metadata['permalink'],
                                            missed_it_string)

        cube_text_preface = "For those of you that %s:" % missed_it_string

    dbh = _getDBHandle()
    stmt = """SELECT sucks,
                      datetime(slurp_date, 'unixepoch') as SLURP_DATE_STRING,
                      SLURP_BY
                    FROM cubes
                    WHERE id=:id;"""
    res = dbh.execute(stmt, {"id": cube_num})
    cubelist = res.fetchall()
    dbh.close()

    if (len(cubelist) != 1):
        raise Exception("cubeInfo: %d results for lookup of %d" % (len(cubelist), cube_num))

    row = cubelist[0]
    score = row['sucks']
    slurp_date = row['SLURP_DATE_STRING']
    slurper = row['SLURP_BY']

    cube_text = getCubeContent(cube_num)

    # Unfurl set to false (ignored on zulip), so that we
    # don't display the cube content in line twice on slack.
    sendReply(
          "Cube %d was slurped on %s by %s.\n" \
	      "It has %d sucks votes.\n\n%s\n" \
          "%s" % (cube_num, slurp_date, slurper, score,
                  cube_text_preface, cube_text),
          unfurl=False)


def slurpCube(sender, message, mistakeMessage, sendReply, display_sender=None):
    '''Slurp a cube!'''
    # NOTE:  Perl zdaemon used a flock() here to protect
    # the database and cubecount file together.  However,
    # we just query the database for the cube count.
    #
    # So, as long as we write the text of the cube to the
    # filesystem first, we'll always have a consistent state.
    # If we crash before the database is updated, then we do
    # leave an extra cube file around, but we don't ever
    # reference it and it will be overwritten on the
    # next slurp.

    # Transaction here because we want a very specific
    # handling of the id field (no gaps, always use the
    # next one).
    if display_sender is None:
        display_sender = sender

    m = re.search(r'\w', message)
    if not m:
        sendReply('Give me a little more to work with please, %s' % display_sender)
        return

    next_cube = -1
    dbh = _getDBHandle()
    cur = dbh.cursor()
    cur.execute("BEGIN")
    try:
        next_cube = _getCountWithCursor(cur) + 1

        with open(("%s/cube.%d" % (CUBEDIR, next_cube)), "w") as f:
            f.write(message)

        stmt = """INSERT INTO cubes
                  (id, sucks, slurp_date, slurp_by)
                  VALUES
                  (:id, 0, strftime('%s', 'now'), :slurp_by);"""
        cur.execute(stmt, {"id": next_cube, "slurp_by": sender})

        cur.execute("COMMIT")
    except Exception as e:
        cur.execute("ROLLBACK")
        raise e
    finally:
        dbh.close()

    sendReply(
          "Cube slurped. You're #%d.\n\n" \
          "Mistake?  %s" % (next_cube, mistakeMessage))


LAST_UNSLURP_TIME = 0
def unslurpCube(sender, fullsender, sendReply):
    '''Unslurp a cube.
       Restrictions:
         can only be done by original slurper
         can only be done within 60 minutes of original slurp

       MAINTAINER can get around those restrictions (up to 1 day)

       Once one unslurp succeeds, we won't process anything for 60 seconds.  This
       somewhat protects us from slack replaying message events to us if a user
       tried several times after seeing no response (especially if an admin!), and
       then the bot comes up and sees all the replays.  Sadly, the websocket API
       doesn't appear to provide retry detection directly while the bot is down
       (TODO: if it does -- including when the bot is just down, please use it
       instead and just reject _all_ retries!).  This timeout is not persistent
       across restarts.

       For DB consistency reasons, we only allow unslurping of the
       most recent cube.  (count and max cube # must match)

       Note that this can unslurp multiple cubes in sequence.
       Thats probably fine.
    '''
    cube_num = -1
    admin_override = False

    global LAST_UNSLURP_TIME
    this_unslurp_time = int(time.time())
    if LAST_UNSLURP_TIME + 60 > this_unslurp_time:
        sendReply("Proccessed a successful unslurp too recently, they're a big deal.\n"
                  "Give it a minute and try again.")
        return

    dbh = _getDBHandle()
    cur = dbh.cursor()
    cur.execute("BEGIN")
    try:
        # What cube are we unslurping?
        cube_num = _getCountWithCursor(cur)

        # Load its DB entry
        res = cur.execute(
            """SELECT strftime('%s', 'now') - slurp_date AS interval,
                      slurp_by
                FROM cubes
                WHERE id=:id;""", {"id": cube_num})
        rows = res.fetchall()
        if len(rows) != 1:
            cur.execute("ROLLBACK")
            sendReply('Hmmm.  I was not able to find cube #%d' % cube_num)
            return

        original_sender = rows[0]['slurp_by']
        time_interval = int(rows[0]['interval'])
        is_admin = is_maintainer(fullsender)

        if (original_sender != sender and not is_admin):
            cur.execute("ROLLBACK")
            sendReply(
                  "Attempt to unslurp cube #%d by %s failed.\n\n" \
                  "Sorry, only the original sender, %s can unslurp this cube." \
                  % (cube_num, sender, original_sender))
            return
        elif (original_sender != sender and is_admin):
            admin_override = True

        if (time_interval > 3600 and not is_admin):
            cur.execute("ROLLBACK")
            sendReply(
                  "Attempt to unslurp cube #%d by %s failed.\n\n" \
                  "Sorry, you can only unslurp a cube within an hour of slurping it.\n" \
                  "It has been %d seconds." \
                  % (cube_num, sender, time_interval))
            return
        elif (time_interval > 86400 and is_admin):
            cur.execute("ROLLBACK")
            # Protect from dumb admins.
            sendReply(
                  "Attempt to unslurp cube #%d by %s failed.\n\n" \
                  "Sorry, even admins can only unslurp a cube within a day of it being slurped.\n" \
                  "It has been %d seconds." \
                  % (cube_num, sender, time_interval))
            return
        elif (time_interval > 3600 and is_admin):
            admin_override = True

        # We are good to remove it.  Wiping from the DB is sufficient.
        cur.execute("DELETE FROM CUBES WHERE id=:id", {"id": cube_num})
        cur.execute("COMMIT")

        # Ok, mark that we succeeded.
        LAST_UNSLURP_TIME = this_unslurp_time
    except Exception as e:
        cur.execute("ROLLBACK")
        raise e
    finally:
        dbh.close()

    # TODO: This leaves the cube text on the filesystem, but it
    # will be overwritten on the next slurp.  For now, that
    # is acceptable, but it does make it compelling to put the
    # cube text directly in the database instead.

    sendReply(
          "Cube #%d unslurped by %s.%s" \
          % (cube_num, sender, " (Admin Override)" if admin_override else ""))


def cubeActivity(sendReply):
    dbh = _getDBHandle()

    stmt = """SELECT count(*) AS count, count(*)/10 AS dots,
                     strftime("%Y",SLURP_DATE,'unixepoch') AS year
                FROM cubes
                GROUP BY year
                ORDER BY year;"""
    rows = dbh.execute(stmt).fetchall()
    dbh.close()

    msg = "```\n"
    for row in rows:
        year = int(row['year'])
        count = int(row['count'])
        dotstring = ""
        for i in range(row['dots']):
            dotstring += ">"
        msg += "%d: %s (%d)\n" % (year, dotstring, count)
    msg += "```"

    sendReply(msg)


def cubeStats(sendReply):
    dbh = _getDBHandle()

    # TODO: It might be possible to do this all
    # in a query instead of manually, but the
    # aggregate sum of cubes score is a problem.
    stmt = """SELECT sucks, slurp_by FROM cubes;"""
    rows = dbh.execute(stmt).fetchall()
    dbh.close()

    stats = {}
    sucks = {}
    sucks_cubed = {}
    for row in rows:
        user = realID(row['slurp_by'])
        sucks_val = row['sucks']

        stats.setdefault(user, 0)
        sucks.setdefault(user, 0)
        sucks_cubed.setdefault(user, 0)

        stats[user] += 1
        sucks[user] += sucks_val
        sucks_cubed[user] += (sucks_val * sucks_val * sucks_val)

    msg = "The database has %d cubes as follows:\n" % getCount()

    # List of users sorted by most slurps.
    users = sorted(stats.keys(), key=stats.get, reverse=True)

    for user in users:
        count = stats[user]
        user_line = "%s: %d (s/r avg: %.3f, %.3f)\n" \
            % (user, count,
               sucks[user]/count,
               sucks_cubed[user]/count)
        msg += user_line

    sendReply(msg)


def cubeQuery(pattern, sendReply):
    '''Case-sensitve query of the cube database.

       pattern is the pattern to match.  it will be greatly simplified for shell passing.
       sendReply(msg) sends the results to the right place.
    '''
    # TODO: This interface is one of the big questions about bringing
    # cube content into sqlite.  It would mean we can't use grep, and
    # instead are limited to sqlite "LIKE"  This is probably ok
    # given how we constrain the regexp already.
    #
    # It would also mean we don't need to filter out cubes
    # that have technicaly been unslurped.

    pattern = pattern.rstrip()
    clean_pattern = re.sub(r'[^A-Za-z0-9 \.\*]+', '', pattern)

    # Need shell interpretation for the wildcards.
    # grep -l limits output to filenames.
    u = subprocess.Popen(
        "grep -l '%s' %s/cube.*" % (clean_pattern, CUBEDIR),
        shell=True, encoding='ascii', stdout=subprocess.PIPE)
    output, _ = u.communicate()
    files = output.splitlines()

    try:
        dbh = _getDBHandle()
        cur = dbh.cursor()
        max_cube = _getCountWithCursor(cur)
        msg = "All cubes matching /%s/:\n----\n" % clean_pattern
        for filename in files:
            cube_num = -1
            cube_text = ""
            m = re.search(r'/cube\.(\d+)$', filename)
            if m:
                cube_num = int(m.group(1))
            else:
                # Don't know what this file was, but skip it.
                continue

            if (cube_num > max_cube):
                # Technically this cube does not exist, skip.
                # (can be leftover after an unslurp)
                continue

            cube_text = getCubeContent(cube_num)

            rows = cur.execute('SELECT sucks FROM cubes WHERE id=:id;', {"id": cube_num}).fetchall()
            sucks_score = "<<UNKNOWN>>"
            if (len(rows) == 1):
                # Not having exactly 1 row here is an error,
                # but we can ignore it somewhat cleanly.
                sucks_score = rows[0]['sucks']

            msg += "*Cube #%d [Sucks Score: %s]:*\n%s\n----\n" % (cube_num, sucks_score, cube_text)
    finally:
        dbh.close()

    sendReply(msg)

# Parse out arguments for !cubeslurp on Slack.
def slackSlurpCube(message, sendResponse):
    display_sender = '<@%s>' % message['user']
    m = re.search(r"^!(slurpcube|cubeslurp)\s+(.+)$", message['text'], re.DOTALL)
    if m:
        sender = get_slack_user_email(message['user'])
        text = m.group(2)
        slurpCube(sender, text, "Use the `!unslurpcube` command", sendResponse,
                  display_sender = display_sender)
    else:
        sendResponse("I don't quite know what you want me to slurp there, %s." % display_sender)


# Parse out arguments for !cubequery on Slack.
def slackCubeQuery(text, sendResponse):
    m = re.search(r"^!(cubequery)\s+(.+)$", text)
    if m:
        cubeQuery(m.group(2), sendResponse)
    else:
        sendResponse("Please supply a pattern to search for.")


# does all the cube hooks..
def cubeCheck(zclass, instance, sender, fullsender, message):
    if (zclass != cfg.ABTECH_CLASS and
        zclass != cfg.ZDAEMON_CLASS):
        # Irrelevant class for cubes, no need to proceed.
        return

    if (zclass == cfg.ZDAEMON_CLASS and
        (instance == "cube.gimme" or instance == "cube.gimmie")):
        cubeGimmeZulip(sender, message)
        return

    if (zclass == cfg.ZDAEMON_CLASS and
        instance == "cube.info"):
        sendResponse = functools.partial(sendz, cfg.ZDAEMON_CLASS, "cube.info")
        cubeInfo(message, sendResponse)
        return

    if (instance == "cube.sucks"):
        sendResponse = functools.partial(sendz, cfg.ZDAEMON_CLASS, "cube.sucks")
        cubeSucksZulip(1, sender, sendResponse)
        return

    if (instance == "cube.rocks"):
        sendResponse = functools.partial(sendz, cfg.ZDAEMON_CLASS, "cube.rocks")
        cubeSucksZulip(-1, sender, sendResponse)
        return

    if (instance == "cube.slurp"):
        sendResponse = functools.partial(sendz, cfg.ZDAEMON_CLASS, "cube.slurped")
        slurpCube(sender, message, "Send a message to the cube.unslurp instance.", sendResponse)
        return

    if (instance == "cube.unslurp"):
        sendResponse = functools.partial(sendz, cfg.ZDAEMON_CLASS, "cube.unslurp")
        unslurpCube(sender, fullsender, sendResponse)
        return

    if (zclass == cfg.ZDAEMON_CLASS and
        instance == "cube.stats"):
        sendResponse = functools.partial(sendz, cfg.ZDAEMON_CLASS, "cube.stats")
        cubeStats(sendResponse)
        return

    if (zclass == cfg.ZDAEMON_CLASS and
        instance == "cube.activity"):
        sendResponse = functools.partial(sendz, cfg.ZDAEMON_CLASS, "cube.activity")
        cubeActivity(sendResponse)
        return

    if (zclass == cfg.ZDAEMON_CLASS and
        instance == "cube.query"):
        sendResponse = functools.partial(sendz, cfg.ZDAEMON_CLASS, "cube.query")

        # On zulip, the entire message is the pattern.
        cubeQuery(message, sendResponse)
        return

    if (zclass == cfg.ZDAEMON_CLASS and
        instance == "cube.service"):
        sendResponse = functools.partial(sendz, cfg.ZDAEMON_CLASS, "cube.service")
        cubeService(sendResponse)
        return


def cubeSlackRouter(message):
    text = message['text']
    thread = get_slack_thread(message)

    # TODO: Might not always be correct
    sendResponse = functools.partial(sendsText, message['channel'], thread_ts=thread)

    if(re.search(r"^!(cubegimme|cubegimmie)($|\s)", text)):
        cubeGimmeSlack(message)

    if (re.search(r"^!cubeinfo($|\s)", text)):
        # TODO: Intercept parameterless command being sent to a thread other than the
        # most recent cube thread.
        cubeInfo(text, sendResponse, slack_channel=message['channel'])

    if (re.search(r"^!cubesucks($|\s)", text)):
        cubeSucksSlack(1, message, sendResponse)

    if (re.search(r"^!cuberocks($|\s)", text)):
        cubeSucksSlack(-1, message, sendResponse)

    if (re.search(r"^!(slurpcube|cubeslurp)($|\s)", text)):
        slackSlurpCube(message, sendResponse)

    if (re.search(r"^!(csa|c\.s\.a\.?)($|\s)", text)):
        sendResponse("OK <@%s>, I almost slurped that, but I didn't." % message['user'])

    if (re.search(r"^!(unslurpcube|cubeunslurp)($|\s)", text)):
        userid = message['user']
        sender = get_slack_user_email(userid)
        fullsender = get_slack_user_email(userid, lhs_only=False)
        unslurpCube(sender, fullsender, sendResponse)

    if (re.search(r"^!(cubestats)($|\s)", text)):
        cubeStats(sendResponse)

    if (re.search(r"^!(cubeactivity)($|\s)", text)):
        cubeActivity(sendResponse)

    if (re.search(r"^!(cubequery)($|\s+)", text)):
        slackCubeQuery(text, sendResponse)

    if (re.search(r"^!(cubeservice)($|\s)", text)):
        cubeService(sendResponse)
