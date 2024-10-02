# Message triggers
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
import re
import time
from random import randrange
from typing import Callable

from jinja2 import Environment
from jinja2.exceptions import TemplateSyntaxError
from yaml import load as yaml_load

try:
    from yaml import CSafeLoader as YamlSafeLoader
except ImportError:
    from yaml import YamlSafeLoader

from cube import SendableCube
from common import get_slack_thread, get_slack_user_email, sendsText

# Modified from Home Assistant's custom regex filters for Jinja
# https://github.com/home-assistant/core/blob/33ff6b5b6ee3d92f4bb8deb9594d67748ea23d7c/homeassistant/helpers/template.py#L2106-L2143
_regex_cache = functools.cache(
    re.compile
)  # Unbounded cache since finite number of regexes per config


def template_regex_match(value, find="", ignorecase=False):
    """Match value using regex."""
    if not isinstance(value, str):
        value = str(value)
    flags = re.I if ignorecase else 0
    return bool(_regex_cache(find, flags).match(value))


def template_regex_search(value, find="", ignorecase=False):
    """Search using regex."""
    if not isinstance(value, str):
        value = str(value)
    flags = re.I if ignorecase else 0
    return bool(_regex_cache(find, flags).search(value))


class ZdaemonMessageMatchTriggers:
    """
    --Combination DRINKBOT and DUHBOT--
    ABTech Drinking Game by adamp@abtech.org
    Based on suggestions by class abtech
    This will need to get taken out if it slows down zdaemon too much.
    It lenghtens the amount of processing per zephyr quite a bit.
      -rjs3 in 2024: lol
    """

    def __init__(self, trigger_config_path: str = "triggers.yaml"):
        self.jinja_env = Environment()
        self.jinja_env.filters = self.jinja_env.filters | {
            "regex_match": template_regex_match,
            "regex_search": template_regex_search,
        }
        # Only load trigger config once at class initialization
        with open(trigger_config_path, "r", encoding="utf-8") as stream:
            self.trigger_config = yaml_load(stream, Loader=YamlSafeLoader)
        self.timeout_s = self.trigger_config["trigger_timeout_s"]
        self.special_last_trigger_s = (
            -1 * self.timeout_s
        )  # You know, just in case computer time is 0
        if not self.check_all_syntax():
            raise RuntimeError("Syntax error in one or more trigger templates")

    def check_and_record_timeout(self) -> bool:
        """Check if we have done a "special" recently.  If so,
        return False.  If not, mark the special var
        return True.

        timeout is specified in seconds in __init__().

        NOTE: This check is irrelevant for Zulip since the class is re-
        initialized at every message event.
        """
        nowtime_s = int(time.time())

        if self.special_last_trigger_s + self.timeout_s >= nowtime_s:
            # Skip, we just did something.
            return False

        self.special_last_trigger_s = nowtime_s

        return True

    def slack_check_msg(self, message_obj) -> None:
        """
        Take in a Slack message and pass it to check_msg()
        """
        sender = get_slack_user_email(message_obj["user"])
        channel = message_obj[
            "channel"
        ]  # I want to assume this is a string, but of course it won't be
        ts = get_slack_thread(message_obj)
        text = message_obj["text"]
        display_sender = f'<@{ message_obj["user"] }>'

        def respond_with_cube():
            SendableCube().sendSlack(channel=channel, thread_ts=ts)

        def respond_text(_, text):
            sendsText(channel, text, thread_ts=message_obj["ts"])

        self.check_msg(
            "slack",
            sender,
            text,
            respond_with_cube,
            respond_text,
            display_sender=display_sender,
            channel=channel,
        )

    def check_msg(  # pylint: disable=too-many-arguments
        self,
        instance: str,
        sender: str,
        message: str,
        send_cube: Callable,
        reply: Callable,
        display_sender: str = None,
        channel: str = None,
    ) -> None:
        """
        All autoresponses

        instance is the zulip topic of the message, but is only used for some
        replies and for one of the "quiet" checks (which has slightly different
        behavior when instance is "slack") sender is the LHS of the email of
        the sender message is the message we are checking.

        send_cube is a zero-argument function to send a random cube to the
        appropriate place. reply takes (instance, message), though you are free
        to ignore instances.

        display_sender is text to use instead of sender in responses, if None,
        we use sender.
        """
        if display_sender is None:
            display_sender = sender
        template_vars = {
            "instance": instance,
            "channel": channel,
            "sender": sender,
            "display_sender": display_sender,
            "message": message,
        }
        for trigger in self.trigger_config["triggers"]:
            # We render each trigger test's Jinja with the context vars for a
            # message and then the Jinja expression (which is in a string)
            # returns True or False. We expect an exact match of "True" instead
            # of casting so that erroneous tests that return anything other
            # than "False" do not trigger.
            if (
                self.jinja_env.from_string(trigger["test"]).render(
                    **template_vars
                )
                == "True"
            ):
                if (
                    "enforce_special_timeout" not in trigger
                    or not trigger["enforce_special_timeout"]
                    or self.check_and_record_timeout()
                ):
                    self.send_response(trigger, template_vars, reply)
                    if "send_cubes_count" in trigger:
                        for _ in range(trigger["send_cubes_count"]):
                            time.sleep(1)
                            send_cube()

    def send_response(
        self, trigger: dict, template_vars: dict, reply: Callable
    ) -> None:
        """
        Given a match trigger, send a message response
        """
        reply_template = None
        rand_num = randrange(100)
        total_count = 0
        for probability, template in trigger["response"].items():
            if probability != "default":
                if total_count <= rand_num < total_count + probability:
                    reply_template = template
                    break
                total_count += probability
        if reply_template is None and "default" in trigger["response"]:
            reply_template = trigger["response"]["default"]
        if reply_template is not None:
            reply_msg = self.jinja_env.from_string(reply_template).render(
                **template_vars
            )
            reply_instance = self.jinja_env.from_string(
                trigger["legacy_instance"]
            ).render(**template_vars)
            reply(reply_instance, reply_msg)

    def check_all_syntax(self) -> bool:
        """
        Check all test, legacy_instance, and response options for syntax errors
        """
        result = True
        for trigger in self.trigger_config["triggers"]:
            if not self.check_syntax("test", trigger["test"]):
                result = False

            if not self.check_syntax(
                "legacy_instance", trigger["legacy_instance"]
            ):
                result = False

            for key, response in trigger["response"].items():
                if not self.check_syntax(f'response "{key}"', response):
                    result = False
        return result

    def check_syntax(self, name: str, template: str) -> bool:
        """
        Check a template for syntax errors.
        """
        template_vars = {
            "instance": "slack",
            "channel": "123456",
            "sender": "zdaemon",
            "display_sender": "<@zdaemon>",
            "message": "I'm just a test!",
        }
        try:
            self.jinja_env.from_string(template).render(**template_vars)
        except TemplateSyntaxError as err:
            # Log the template so we know which one has a syntax error
            print(f"Syntax error in { name } template: { template }")
            print(err)
            return False
        return True
