import os
import tuned.logs
import base
from tuned.utils.commands import commands

log = tuned.logs.get()

class cpulist_invert(base.Function):
	"""
	Inverts list of CPUs (makes its complement). For the complement it
	gets number of present CPUs from the /sys/devices/system/cpu/present,
	e.g. system with 4 CPUs (0-3), the inversion of list "0,2,3" will be
	"1"
	"""
	def __init__(self):
		# arbitrary number of arguments
		super(self.__class__, self).__init__("cpulist_online", 0)

	def execute(self, args):
		if not super(self.__class__, self).execute(args):
			return None
		cpus = self._cmd.cpulist_unpack(",".join(args))
		present = self._cmd.cpulist_unpack(self._cmd.read_file("/sys/devices/system/cpu/present"))
		return ",".join(str(v) for v in set(present) - set(cpus))
