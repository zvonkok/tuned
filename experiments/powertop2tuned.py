#!/usr/bin/python -Es
#
# Copyright (C) 2008-2012 Red Hat, Inc.
# Authors: Jan Kaluza <jkaluza@redhat.com>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#

import os
import sys
import tempfile
import shutil
import argparse
from subprocess import *
from HTMLParser import HTMLParser
from htmlentitydefs import name2codepoint

SCRIPT_SH = """#!/bin/sh

. /usr/lib/tuned/functions

start() {
%s
	return 0
}

stop() {
%s
	return 0
}

process $@
"""

TUNED_CONF_PROLOG = "# Automatically generated by powertop2tuned tool\n\n"
TUNED_CONF_INCLUDE = """[main]
%s\n
"""
TUNED_CONF_EPILOG="""\n[powertop_script]
type=script
replace=1
script=script.sh
"""


class PowertopHTMLParser(HTMLParser):
	def __init__(self, enable_tunings):
		HTMLParser.__init__(self)

		self.inProperTable = False
		self.inScript = False
		self.intd = False
		self.lastStartTag = ""
		self.tdCounter = 0
		self.lastDesc = ""
		self.data = ""
		self.currentScript = ""
		if enable_tunings:
			self.prefix = ""
		else:
			self.prefix = "#"

		self.plugins = {}

	def getParsedData(self):
		return self.data

	def getPlugins(self):
		return self.plugins

	def handle_starttag(self, tag, attrs):
		self.lastStartTag = tag
		if self.lastStartTag == "div" and dict(attrs)["id"]  == "tuning":
			self.inProperTable = True
		if self.inProperTable and tag == "td":
			self.tdCounter += 1
			self.intd = True

	def parse_command(self, command):
		prefix = ""
		command = command.strip()
		if command[0] == '#':
			prefix = "#"
			command = command[1:]

		if command.startswith("echo") and command.find("/proc/sys") != -1:
			splitted = command.split("'")
			value = splitted[1]
			path = splitted[3]
			path = path.replace("/proc/sys/", "").replace("/", ".")
			self.plugins.setdefault("sysfs", "[sysfs]\ndynamic_tuning=0\n")
			self.plugins["sysfs"] += "#%s\n%s%s=%s\n\n" % (self.lastDesc, prefix, path, value)
		# TODO: plugins/plugin_sysfs.py doesn't support this so far, it has to be implemented to 
		# let it work properly.
		elif command.startswith("echo") and (command.find("'/sys/") != -1 or command.find("\"/sys/") != -1):
			splitted = command.split("'")
			value = splitted[1]
			path = splitted[3]
			if path == "/sys/module/snd_hda_intel/parameters/power_save":
				self.plugins.setdefault("audio", "[audio]\ndynamic_tuning=0\n")
				self.plugins["audio"] += "#%s\n%shda_intel_powersave=1\n" % (self.lastDesc, prefix)
			else:
				self.plugins.setdefault("sysfs", "[sysfs]\ndynamic_tuning=0\n")
				self.plugins["sysfs"] += "#%s\n%s%s=%s\n\n" % (self.lastDesc, prefix, path, value)
		elif command.startswith("ethtool -s ") and command.endswith("wol d;"):
			self.plugins.setdefault("net", "[net]\ndynamic_tuning=0\n")
			self.plugins["net"] += "#%s\n%swake_on_lan=0\n" % (self.lastDesc, prefix)
		else:
			return False
		return True

	def handle_endtag(self, tag):
		if self.inProperTable and tag == "table":
			self.inProperTable = False
			self.intd = False
		if tag == "tr":
			self.tdCounter = 0
			self.intd = False
		if tag == "td":
			self.intd = False
		if self.inScript:
			#print self.currentScript
			self.inScript = False
			# Command is not handled, so just store it in the script
			if not self.parse_command(self.currentScript.split("\n")[-1]):
				self.data += self.currentScript + "\n\n"

	def handle_entityref(self, name):
		if self.inScript:
			self.currentScript += unichr(name2codepoint[name])

	def handle_data(self, data):
		prefix = self.prefix
		if self.inProperTable and self.intd and self.tdCounter == 1:
			self.lastDesc = data
			if self.lastDesc.lower().find("autosuspend") != -1 and (self.lastDesc.lower().find("keyboard") != -1 or self.lastDesc.lower().find("mouse") != -1):
					self.lastDesc += "\n\t# WARNING: For some devices, uncommenting this command can disable the device."
					prefix = "#"
		if self.intd and ((self.inProperTable and self.tdCounter == 2) or self.inScript):
			self.tdCounter = 0
			if not self.inScript:
				self.currentScript += "\t# " + self.lastDesc + "\n"
				self.currentScript += "\t" + prefix + data.strip()
				self.inScript = True
			else:
				self.currentScript += data.strip()

class PowertopProfile:
	BAD_PRIVS = 100
	PARSING_ERROR = 101
	BAD_SCRIPTSH = 102

	def __init__(self, output, name = ""):
		self.name = name
		self.output = output

	def currentActiveProfile(self):
		proc = Popen(["tuned-adm", "active"], stdout=PIPE)
		output = proc.communicate()[0]
		if output and output.find("Current active profile: ") == 0:
			return output[len("Current active profile: "):output.find("\n")]
		return "unknown"

	def checkPrivs(self):
		myuid = os.geteuid()
		if myuid != 0:
			print >> sys.stderr, 'Run this program as root'
			return False
		return True

	def generateHTML(self):
		print "Running PowerTOP, please wait..."
		proc = Popen("LANG= powertop --html=/tmp/powertop --time=1", stdout=PIPE, stderr=PIPE, shell=True)
		output = proc.communicate()[1]
		if proc.returncode != 0:
			return ret

		prefix = "PowerTOP outputing using base filename "
		if output.find(prefix) == -1:
			return -1

		name = output[output.find(prefix)+len(prefix):-1]
		#print "Parsed filename=", [name]
		return name

	def parseHTML(self, enable_tunings):
		f = open(self.name)
		parser = PowertopHTMLParser(enable_tunings)
		parser.feed(f.read())
		f.close()

		return parser.getParsedData(), parser.getPlugins()

	def generateShellScript(self, profile, data):
		print "Generating shell script", os.path.join(self.output, "script.sh")
		f = open(os.path.join(self.output, "script.sh"), "w")
		f.write(SCRIPT_SH % (data, ""))
		os.fchmod(f.fileno(), 0755)
		f.close()
		return True

	def generateTunedConf(self, profile, new_profile, plugins):
		print "Generating Tuned config file", os.path.join(self.output, "tuned.conf")
		f = open(os.path.join(self.output, "tuned.conf"), "w")
		f.write(TUNED_CONF_PROLOG)
		if not new_profile:
			f.write(TUNED_CONF_INCLUDE % ("include=" + profile))

		for plugin in plugins.values():
			f.write(plugin + "\n")

		f.write(TUNED_CONF_EPILOG)
		f.close()

	def generate(self, new_profile, enable_tunings):
		generated_html = False
		if len(self.name) == 0:
			generated_html = True
			if not self.checkPrivs():
				return self.BAD_PRIVS

			name = self.generateHTML()
			if isinstance(name, int):
				return name
			self.name = name

		data, plugins = self.parseHTML(enable_tunings)

		if generated_html:
			os.unlink(self.name)

		if len(data) == 0 and len(plugins) == 0:
			print >> sys.stderr, 'Your Powertop version is incompatible (maybe too old) or the generated HTML output is malformed'
			return self.PARSING_ERROR

		profile = self.currentActiveProfile()

		if not os.path.exists(self.output):
			os.makedirs(self.output)

		if not self.generateShellScript(profile, data):
			return self.BAD_SCRIPTSH

		self.generateTunedConf(profile, new_profile, plugins)

		return 0

if __name__ == "__main__":
	parser = argparse.ArgumentParser(description='Creates Tuned profile from Powertop HTML output.')
	parser.add_argument('profile', metavar='profile_name', type=unicode, nargs='?', help='Name for the profile to be written.')
	parser.add_argument('-i', '--input', metavar='input_html', type=unicode, help='Path to Powertop HTML report. If not given, it is generated automatically.')
	parser.add_argument('-o', '--output', metavar='output_directory', type=unicode, help='Directory where the profile will be written, default is /etc/tuned/profile_name directory.')
	parser.add_argument('-n', '--new-profile', action='store_true', help='Creates new profile, otherwise it merges (include) your current profile.')
	parser.add_argument('-f', '--force', action='store_true', help='Overwrites the output directory if it already exists.')
	parser.add_argument('--enable', action='store_true', help='Enable all tunings (not recommended). Even with this enabled tunings known to be harmful (like USB_AUTOSUSPEND) won''t be enabled.')
	args = parser.parse_args()
	args = vars(args)

	if not args['profile'] and not args['output']:
		print >> sys.stderr, 'You have to specify the profile_name or output directory using the --output argument.'
		parser.print_help()
		sys.exit(-1)

	if not args['output']:
		args['output'] = "/etc/tuned"	

	if args['profile']:
		args['output'] = os.path.join(args['output'], args['profile'])

	if not args['input']:
		args['input'] = ''

	if os.path.exists(args['output']) and not args['force']:
		print >> sys.stderr, 'Output directory already exists, use --force to overwrite it.'
		sys.exit(-1)

	p = PowertopProfile(args['output'], args['input'])
	sys.exit(p.generate(args['new_profile'], args['enable']))

