This is zdaemon.

It is the python version, since few have survived looking at the legacy perl version.

# Configuration

Configuration of maintainers, directories, and class name need to be made in `zdaemon.json` (see sample file).  Additionally, a `zuliprc` from the bot config on zulip must be provided (possibly via the `--config-file` parameter)

It is recommended you get a baseline set of the data files from production to get a good sample of data to work with.  Failing this, you will need to create `ppdata.sqlite` and `cube.sqlite` using the schemas described in `plusplus.py` and `cube.py` respectively.  You will also want to have at least one cube slurped before things start to act normal.  Finally, sending a single cube may be required to fully fill in the data.

# Docker Deployment

Deployments need to provide the `zuliprc`, `zdaemon.json`, and `triggers.yaml` config files, as well as the data directory where zdaemon will keep its databases.

 - Build the image
   - `docker build -t zdaemon .`
 - Run the image (something like this interactive/attached version).  Note the config files can be read only.
   - `docker run -v ./data:/home/zdaemon/data -v ./zdaemon.json:/home/zdaemon/zdaemon.json:ro -v /home/zdaemon/.zuliprc:/home/zdaemon/zuliprc:ro -v ./triggers.yaml:/home/zdaemon/triggers.yaml:ro -w /home/zdaemon -it zdaemon`

There is a sample Docker Compose YAML file you can also examine, `compose.yml.sample`.

# Slack Bot Config

To configure the slack bot, create a new app at https://api.slack.com.  The repository has a sample manifest describing the needed scopes and permissions in `slack-bot.yaml.sample` (but you probably want to change the bot name).

# Triggers

The production triggers file is not available in the public repository, however a sample file
(`triggers.yaml.sample`) is available to see how to create new triggers.  

# Credits

- zdaemon was originally authored by Kevin Miller in December of 1998.
- From about 2005-2023, minor updates including transitioning it to support Zulip were performed by Adam Pennington and Chris Tuttle
- In 2024 a full rewrite to Python was done by Rob Siemborski.  Shortly thereafter, support for slack was added.
- Perry Naseck has also made various contributions, including the external config for triggers.

# License

```
Copyright (c) 2024, AB Tech
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```
