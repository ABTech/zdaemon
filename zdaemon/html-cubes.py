#!/usr/bin/python3
# Generate the web page with a summary of all cubes.
# Abuses internal cube module functions.
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

import argparse

import config as cfg
import cube
from common import realID

parser = argparse.ArgumentParser()
cfg.add_zdaemon_arguments(parser)

options = parser.parse_args()
cfg.init_zdaemon_config(options, load_channels=False, config_file_only=True)

cubelist = []

try: 
  dbh = cube._getDBHandle()

  stmt = '''SELECT id, sucks,
            datetime(slurp_date, 'unixepoch') AS SLURP_DATE_STRING,
            SLURP_BY
            FROM cubes
            ORDER BY id;'''
  cubelist = dbh.execute(stmt).fetchall()
finally:
  dbh.close()

print("<html><head>")
print("<meta http-equiv=\"Content-Type\" content=\"text/html; charset=utf-8\">")
print("<title>Cube Database</title>")
print("</head>")
print("<body bgcolor=white><h1>ABTech Cubes</h2>\n")
print("This is just a dump of the cubes database.<br>\n")

for row in cubelist:
  print("<a name=\"cube%d\">" % row["id"])
  print("<table border=1><tr bgcolor=lightblue><td><b># %d</b></td>" % row["id"])
  print("<td>Slurped by <b>%s</b> on <b>%s</b></td>" % (realID(row['slurp_by']), row['slurp_date_string']))

  sucks_color = ""
  if row['sucks'] >= 12:
    sucks_color = " bgcolor=indianred"
  elif row['sucks'] >= 6:
    sucks_color = " bgcolor=lightsalmon"
  elif row['sucks'] <= -12:
    sucks_color = " bgcolor=green"
  elif row['sucks'] <= -6:
    sucks_color = " bgcolor=palegreen"

  print("<td%s>Sucks score: %d</td></tr>\n" % (sucks_color, row['sucks']))

  cube_file = cube.CUBEDIR + "/cube.%d" % row["id"]
  with open (cube_file, "r", errors='replace') as f:
    cube_text = f.read()
    print("<tr><td colspan=3><pre>" + ''.join(cube_text) + "</pre></td>")

  print("</tr></table></a><hr>\n")
