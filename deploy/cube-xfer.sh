#! /bin/sh
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

ZULIP_CONFIG_FLAG=--config-file=zuliprc
ROOTDIR=/home/zdaemon
LOGFILE=$ROOTDIR/data/htmlcube.log
OUTPUTDIR=$ROOTDIR/www
OUTPUTFILE=$OUTPUTDIR/cube.html

# Log that we ran.
date >> $LOGFILE

# Create output directory if it is missing
mkdir -p $OUTPUTDIR

# Stderr redirected to the log file, just in case.
python $ROOTDIR/html-cubes.py $ZULIP_CONFIG_FLAG > $OUTPUTFILE.new 2>> $LOGFILE

if [ -s $OUTPUTFILE.new ]; then
    # all good
    mv $OUTPUTFILE.new $OUTPUTFILE
else
    # file is empty, notify maintainer but don't move it
    echo zero byte $OUTPUTFILE.new in cube html output | python $ROOTDIR/notify-maintainer.py $ZULIP_CONFIG_FLAG
fi
