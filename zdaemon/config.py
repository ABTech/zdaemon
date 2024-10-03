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

# System
import json
import os

# Zdaemon
import common
import cube
import plusplus

# Slack/Zulip
from slack_sdk import WebClient
import zulip

# Globally Interesting Variables
ZDAEMON_ROOT = "/home/zdaemon"
ZDAEMON_DATA_DIR = ZDAEMON_ROOT + "/data"
ABTECH_CLASS = "abtech"
ZDAEMON_CLASS = "zdaemon"
GHOSTS_CLASS = "ghosts"
MY_ID = "cube-bot@andrew.cmu.edu"
MAINTAINER = "zdaemon@abtech.org"
SLACK_MAINTAINER = None  # userid of the Slack user with the MAINTAINER address as of startup.

SLACK_ENABLE = False
SLACK_APP_TOKEN = ""
SLACK_BOT_TOKEN = ""
SLACK_CUBE_CHANNEL_ID = ""  # Forced to uppercase
SLACK_BRIDGE_BOT_ID = ""

SENDCUBE_ENABLE = True

# Map of our allowed channels from their ID to their name without a hash.
#
# computed at startup and _not_ recomputed over time, renaming a channel will
# not affect this until zdaemon restarts.
SLACK_CHANNEL_WHITELIST_MAP = {}

_CONFIG_FILE = None

def add_zdaemon_arguments(parser):
    zulip.add_default_arguments(parser)

    group = parser.add_argument_group("zdaemon runtime configuration")
    group.add_argument("--zconfig-file", dest="zconfig_file",
                       help="Zdaemon JSON config file (default: zdaemon.json)",
                       default='zdaemon.json')
    return parser


def init_zdaemon_config(options, load_channels=True, config_file_only=False):
    ''' Call this to set up most of the zdaemon config, both from command
        line options (provided in the parameter) and from the JSON file.

        A slack or zulip client will be created and passed to common.py.

        load_channels controls if, on slack, we initialize the channel
        whitelist which can be expensive if there are a large number
        of channels on the server (and isn't needed for, say, sending a cube)

        config_file_only controls if we are only reading the config file or if we
        should also initialize a web client and potentially make additional network calls.

        if config_file_only is true, load_channels must be false.
    '''
    global ZDAEMON_ROOT
    global ABTECH_CLASS
    global ZDAEMON_CLASS, GHOSTS_CLASS
    global MY_ID, MAINTAINER
    global ZDAEMON_DATA_DIR

    global SLACK_ENABLE, SLACK_APP_TOKEN, SLACK_BOT_TOKEN
    global SLACK_CUBE_CHANNEL_ID
    global SLACK_BRIDGE_BOT_ID

    global SENDCUBE_ENABLE

    global _CONFIG_FILE
    _CONFIG_FILE = options.zconfig_file

    slack_client = None
    slack_channel_whitelist = []

    if not os.path.isfile(_CONFIG_FILE):
        print("Cannot find config file %s, using default values." % _CONFIG_FILE)
    else:
        file_data = {}
        with open(_CONFIG_FILE, "r") as f:
            file_data = json.load(f)

        have_slack_app_token = False
        have_slack_bot_token = False

        if "ZDAEMON_ROOT" in file_data:
            ZDAEMON_ROOT = file_data["ZDAEMON_ROOT"]
        if "ABTECH_CLASS" in file_data:
            ABTECH_CLASS = file_data["ABTECH_CLASS"]
        if "ZDAEMON_CLASS" in file_data:
            ZDAEMON_CLASS = file_data["ZDAEMON_CLASS"]
        if "GHOSTS_CLASS" in file_data:
            GHOSTS_CLASS = file_data["GHOSTS_CLASS"]
        if "MY_ID" in file_data:
            MY_ID = file_data["MY_ID"]
        if "MAINTAINER" in file_data:
            MAINTAINER = file_data["MAINTAINER"]

        if "SLACK_APP_TOKEN" in file_data and file_data["SLACK_APP_TOKEN"] != "":
            have_slack_app_token = True
            SLACK_APP_TOKEN = file_data["SLACK_APP_TOKEN"]
        if "SLACK_BOT_TOKEN" in file_data and file_data["SLACK_BOT_TOKEN"] != "":
            have_slack_bot_token = True
            SLACK_BOT_TOKEN = file_data["SLACK_BOT_TOKEN"]
        if "SLACK_CUBE_CHANNEL_ID" in file_data:
            SLACK_CUBE_CHANNEL_ID = file_data["SLACK_CUBE_CHANNEL_ID"].upper()
        if "SLACK_CHANNEL_WHITELIST" in file_data:
            wl = file_data["SLACK_CHANNEL_WHITELIST"]
            if not isinstance(wl, list):
                raise Exception("SLACK_CHANNEL_WHITELIST is present but doesn't appear to be a list")
            slack_channel_whitelist = wl
        if "SLACK_BRIDGE_BOT_ID" in file_data:
            # Make sure to force this to uppercase.
            SLACK_BRIDGE_BOT_ID = file_data["SLACK_BRIDGE_BOT_ID"].upper()


        if "SENDCUBE_ENABLE" in file_data:
            sce = file_data["SENDCUBE_ENABLE"]
            if not isinstance(sce, bool):
                raise Exception("SENDCUBE_ENABLE is present but isn't a bool")
            SENDCUBE_ENABLE = sce

    # Computed config variables.
    ZDAEMON_DATA_DIR = ZDAEMON_ROOT + "/data"

    # Give the data dir to the modules so they
    # can precompute their file names.
    cube.init_cube_config(ZDAEMON_DATA_DIR)
    plusplus.init_pp_config(ZDAEMON_DATA_DIR)

    if config_file_only:
        if load_channels:
            raise Exception("load_channels True when config_file_only True is not valid!")
        return

    if have_slack_app_token != have_slack_bot_token:
        # We have one and not the other, misconfigured!
        raise Exception("Only one of Slack APP token and BOT TOKEN provided")
    elif have_slack_app_token and have_slack_bot_token:
        SLACK_ENABLE = True

    # Create Web Client
    slack_client = zulip_client = None
    if SLACK_ENABLE:
        slack_client = WebClient(token=SLACK_BOT_TOKEN)
    else:
        zulip_client = zulip.init_from_options(options)

    common.init_common_config(zulip_client, slack_client)

    # Must be done after init_common_config, as it needs the web client.
    if SLACK_ENABLE:
        _init_slack_computed_config(slack_channel_whitelist, load_channels)


def _init_slack_computed_config(channel_whitelist, load_channels):
    '''Slack config items that need to be computed once we are talking to the API.'''
    global SLACK_CHANNEL_WHITELIST_MAP
    global SLACK_MAINTAINER

    maintainer_obj = common.get_slack_user_by_email(MAINTAINER)
    if maintainer_obj is None:
        raise Exception("slack maintainer '%s' wasn't found via lookup" % MAINTAINER)
    elif 'id' not in maintainer_obj:
        raise Exception("maintainer object missing user id for '%s' [%s]??" % (MAINTAINER, maintainer_obj))
    else:
        SLACK_MAINTAINER = maintainer_obj['id']

    #########################################################
    ## Below here only work on loading the channel whitelist!
    if not load_channels:
        return

    if len(channel_whitelist) < 1:
        raise Exception("Empty SLACK_CHANNEL_WHITELIST when slack is enabled.")
    if SLACK_CUBE_CHANNEL_ID == "":
        raise Exception("Empty SLACK_CUBE_CHANNEL_ID when slack is enabled")

    channel_name_map = common.get_slack_channel_nametoid_map()
    channel_id_map = common.get_slack_channel_idtoname_map()

    # The cube channel must always be on the whitelist, so we stuff its id onto
    # the end just in case. It will be uniquified in the loop regardless.
    channel_whitelist.append(SLACK_CUBE_CHANNEL_ID)

    for c in channel_whitelist:
        id = ""
        name = ""
        if c[0] == '#':
            # If the first character is a hash, remove the hash and treat it as a name.
            name = c.replace(c[0], "", 1)
            if name not in channel_name_map:
                raise Exception("Channel '%s' not found in available slack channels [%s]" % (c, channel_name_map))
            id = channel_name_map[name]
        elif c[0] == 'C' or c[0] == 'G':
            # Channel ID
            id = c
            if c not in channel_id_map:
                raise Exception("Channel '%s' not found in available slack channels [%s]" % (c, channel_id_map))
            name = channel_id_map[c]
        else:
            raise Exception("I don't know what to do with channel '%s' in _init_slack_computed_config.\n" \
                            "Channels must start with a # or be a raw Channel ID." % c)

        SLACK_CHANNEL_WHITELIST_MAP[id] = name



def print_config():
    print("Using zdaemon config file: %s" % _CONFIG_FILE)
    print("Using Config:\n----")
    print("MAINTAINER: '%s'" % MAINTAINER)

    if not SLACK_ENABLE:
        print("\n*** ZULIP CONFIG ***")
        print("ZDAEMON_ROOT: '%s'" % ZDAEMON_ROOT)
        print("ABTECH_CLASS: '%s'" % ABTECH_CLASS)
        print("ZDAEMON_CLASS: '%s'" % ZDAEMON_CLASS)
        print("GHOSTS_CLASS: '%s'" % GHOSTS_CLASS)
        print("MY_ID: '%s'" % MY_ID)
    else:
        print("\n*** Zulip DISABLED due to presense of SLACK_BOT_TOKEN and SLACK_APP_TOKEN")

    if SLACK_ENABLE:
        print("\n*** SLACK CONFIG ***")
        print("SLACK_APP_TOKEN: '%s'" % SLACK_APP_TOKEN)
        print("SLACK_BOT_TOKEN: '%s'" % SLACK_BOT_TOKEN)
        print("SLACK_CUBE_CHANNEL_ID: '%s'" % SLACK_CUBE_CHANNEL_ID)
        print("SLACK_MAINTAINER: '%s'" % SLACK_MAINTAINER)
        print("SLACK CHANNEL WHITELIST: '%s'" % SLACK_CHANNEL_WHITELIST_MAP)

        print("\nAll Visible Channels: %s" % common.get_slack_channel_nametoid_map())
    else:
        print("\n*** Slack Not Configured ***\n")
