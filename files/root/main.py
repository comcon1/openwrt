#!/usr/bin/python

import sys, os, os.path, signal, socket
#from smbus import SMBus
from time import time, sleep, strftime
from fcntl import ioctl
from threading import Thread
from traceback import print_stack

defaults = {
	"tsens"	:	"ta",
	"ttemp" :	0.,
	"thyst" :	0.1,
	"heat" :	"/sys/class/gpio/heat/value",
	"i2c_rst_gpio":	"/sys/class/gpio/i2c_rst/value",
	"button" :	"/sys/class/gpio/button/value",
	"detach" :	0,
	"pidfn" :	"/tmp/main.pid",
	"errfn" :	"/tmp/main.err",
	"logfn" :	"/tmp/sens.log",
	"temp_adt" :	"/sys/bus/i2c/devices/0-0048/temp1_input",
	"temp_htu" :	"/sys/bus/i2c/devices/0-0040/iio:device0/in_temp_input",
	"rh_htu" :	"/sys/bus/i2c/devices/0-0040/iio:device0/in_humidityrelative_input",
	"o2" :		"/sys/bus/i2c/devices/0-0049/in0_input",
	"co2_bus" :	0,
	"co2_addr":	0x15,
	"lcd" :		True,
	"lcdw" :	20,
	"lcdh" :	4,
	"avper":	10,
	"avn":		400,
}

run = 1

class gpio1(object):
	def __init__(self, val):
		if (type(val) is str):
			self.val = val
			return
		gp = "/sys/class/gpio"
		g = "%s/gpio%d" % (gp, val)
		self.val = g + "/value"
		self.dir = g + "/direction"
		if not os.path.exists(self.val):
			with open(gp + "/export", "w") as f:
				f.write(str(val))
		with open(self.dir, "w") as f:
			f.write("out")

	def get(self):
		with open(self.val) as f:
			return int(f.read())

	def set(self, v):
		with open(self.val, "w") as f:
			f.write("1" if v else "0")

class tstat:
	def __init__(self, heat, temp, hyst):
		self.heat = heat
		self.hyst = hyst
		self.off = 0
		self.ts = 0
		self.ns = 0
		self.av = 0
		self.st = hyst * 0.1
		self.cycle = 0
		self.tnow = -1
		self.set(temp)

	def set(self, t):
		self.skip_cycle = 2
		if self.heat.get():
			self.skip_cycle = 1
		self.temp = t
		self.off = 0
		sys.stderr.write(hts() + (": TS0: %s\n" % self))

	def tstat(self, t):
		if t < 0:
			heat.set(0)
			return

		self.ns += 1
		self.ts += t
		self.tnow = t

		if self.skip_cycle == 2 and t - self.temp >= -self.hyst:
			self.skip_cycle = 1
			sys.stderr.write(hts() + (": TS1: %s\n" % self))

		if t + self.off - self.temp <= -self.hyst and not self.heat.get() and self.cycle:
			self.cycle = 0
			self.av = self.ts / self.ns
			self.ns = 0
			self.ts = 0.
			if not self.skip_cycle:
				self.off += self.av - self.temp
			sys.stderr.write(hts() + (": TS2: %s\n" % self))

		if t + self.off - self.temp <= -self.hyst and not self.heat.get():
			self.heat.set(1)
			self.cycle = 1
			if self.skip_cycle:
				self.skip_cycle -= 1
			sys.stderr.write(hts() + (": TS3: %s\n" % self))

		if t + self.off - self.temp >= 0 and self.heat.get():
			self.heat.set(0)
			sys.stderr.write(hts() + (": TS4: %s\n" % self))

	def __str__(self):
		r = "Thermostat: %.2fC, " % self.temp
		r += "T: %.2fC, " % self.tnow
		r += "Heat: %d, " % self.heat.get()
		r += "Thyst: %.2f, " % self.hyst
		r += "Tav: %.2f, " % self.av
		r += "Toff: %.2f, " % self.off
		r += "sk_cyc: %d" % self.skip_cycle
		return r


class sensval:
	def __init__(self, val, label, units, prec):
		self.val = val
		self.label = label
		self.units = units
		self.prec = prec

	def __str__(self):
		if self.val is None:
			return "-1"
		fmt = "%." + str(self.prec) + "f"
		return fmt % self.val

class sensor(object):
	def __init__(self, scale, label, units, prec):
		self.scale = scale
		self.label = label
		self.units = units
		self.prec = prec
		self.rep = 0

	def read(self):
		try:
			v = self.scale * self.raw()
			if self.rep:
				sys.stderr.write(": ".join((hts(), self.label, "OK\n")))
				self.rep = 0
		except Exception as e:
			if not self.rep:
				sys.stderr.write(": ".join((hts(), self.label, str(e))) + "\n")
				self.rep = 1
			v = None

		return sensval(v, self.label, self.units, self.prec)

class insysfs(sensor):
	def __init__(self, path, scale, label, units, prec):
		self.path = path
		super(insysfs, self).__init__(scale, label, units, prec)

	def raw(self):
		with open(self.path) as f:
			val = int(f.read())
		return val

I2C_SLAVE = 0x0703
class t6700(sensor):
	def __init__(self, bus, addr):
		self.cdev, self.addr = ("/dev/i2c-%d" % bus), addr
		self.label = "CO2"
		self.units = "ppm"
		self.prec = 0
		self.scale = 1.
		super(t6700, self).__init__(self.scale, self.label, self.units, self.prec)

	def raw(self):
		fd = os.open(self.cdev, os.O_RDWR)
		try:
			ioctl(fd, I2C_SLAVE, self.addr)
			os.write(fd, "".join(map(chr, (0x04, 0x13, 0x8b, 0x00, 0x01))))
			sleep(0.01)
			r = os.read(fd, 4)
			return ord(r[3]) | (ord(r[2]) << 8)
		except Exception as e:
			raise NameError, str(e)
		finally:
			os.close(fd)

LCD_ESC = "\033["
class clcd:
	def __init__(self, width, height, cdev="/dev/lcd"):
		self.width = width
		self.height = height
		self.cdev = cdev
		self.init()

	def write(self, s):
		with open(self.cdev, "w") as cd:
			cd.write(s)

	def esc(self, s):
		self.write(LCD_ESC + s)

	def clear(self):
		self.esc("2J")

	def home(self):
		self.esc("H")

	def goto(self, x, y):
		self.esc("Lx%dy%d;" % (x, y))

	def init(self):
		# re-init
		self.esc("LI")

		# no cursor
		self.esc("Lc")

		# backlight on
		self.esc("L+")

		self.clear()
		self.home()

def daemonize(errfn):
	if os.fork() > 0:
		sys.exit(0)
	os.setsid()
	if os.fork() > 0:
		sys.exit(0)
	os.umask(0)
	import resource
	maxfd = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
	if (maxfd == resource.RLIM_INFINITY):
		maxfd = 1024
	for fd in range(0, maxfd):
		try:
			os.close(fd)
		except OSError:
			pass

	if errfn:
		sys.stderr = open(errfn, "w", 0)

def hts():
	return strftime("%Y-%m-%d %H:%M:%S")

def lcd_upd(lcd, sv):
	if not lcd:
		return
	kk = ("ta", "co2", "rh", "o2")
	lcd.home()
	lcd.write(hts() + "\n")
	l = 0
	for k in kk:
		s = "%s %s%s" % (sv[k].label, sv[k], sv[k].units)
		s = "%-10s" % s
		l += len(s)
		if l >= lcd.width:
			s += "\n"
			l = 0
		lcd.write(s)

def log_header(log, kk, sens):
	if not log:
		return
	log.write("# time,s\t")
	log.write("\t".join(map(lambda k: "%s,%s" % (sens[k].label, sens[k].units), kk)))
	log.write("\n")

def sdump(log, kk, sv):
	if not log:
		return
	log.write("%.1f " % time())
	log.write("\t".join(map(lambda k: str(sv[k]), kk)))
	log.write("\n")

def watchdog(heat, i2c_rst):
	global tstamp
	rep = 0
	while run:
		if time() - tstamp > 5:
			heat.set(0)

			i2c_rst.set(1)
			sleep(0.1)
			i2c_rst.set(0)

			if not rep:
				sys.stderr.write(hts() + ": main thread hanged\n")
				rep = 1

		else:
			if rep:
				sys.stderr.write(hts() + ": main thread is running\n")
				rep = 0
		sleep(1)

def plot_ascii(d):
	r = "<pre>"
	t0 = int(min(d))
	t1 = 1 + int(max(d))
	for i in range(t1, t0 - 1, -1):
		r += "\n%d " % i
		r += "".join(map(lambda t, i=i: "X" if t >= i and t < i+1 else '.', d))
	r += "</pre>"
	return r

def plot_svg(xy, w, h):
	m = .025 * w
	r = '<svg width="%d" height="%d">' % (w, h)
	x0 = int(min(xy, key=lambda xy: xy[0])[0])
	x1 = 1 + int(max(xy, key=lambda xy: xy[0])[0])
	y0 = int(min(xy, key=lambda xy: xy[1])[1])
	y1 = 1 + int(max(xy, key=lambda xy: xy[1])[1])
	ys = (h - 2 * m) / (y1 - y0)
	xs = (w - 2 * m) / (x1 - x0)

	for x in range(0, x1 - x0,  5 * 60):
		r += '<text x="%d" y="%d">-%dm</text>' % (w - m - xs * x,
							h,
							x / 60)
	for i in range(y0, y1):
		y = h - m - ys * (i - y0)
		r += '<text x="%d" y="%d">%d</text>' % (0, y, i)
		r += '<line x1="%d" y1="%d" x2="%d" y2="%d" ' % (m, y, w - m, y)
		r += 'style="stroke:black;stroke-width:1"/>'

	r += '<polyline style="fill:none;stroke:blue;stroke-width:2" points="'
	r += " ".join(map(lambda xy: "%d,%d" % (m + xs * (xy[0] - x0), h - m - ys * (xy[1] - y0)), xy))
	r += '">'

	r += "</svg>"
	return r

class http_serv:
	def __init__(self, cfg, ts, mlog):
		self.cfg = cfg
		self.tstat = ts
		self.mlog = mlog

	def http_frame(self):
		return '''<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Frameset//EN"
"http://www.w3.org/TR/html4/frameset.dtd">
<html>
<frameset rows="50%, 50%">
<frame src="/sens">
<frame src="/ctl">
</frameset>
</html>
'''

	def http_sens(self):
		global sv, tstamp

		r = '''<html>
<head><meta http-equiv="refresh" content="2"></head>
<body bgcolor="#bae99b">
'''
		r += "<h1>" + hts() + "</h1>"
		r += '<table style="width: 100%; font-size: 150%"><tr><td>'

		if sv:
			r += "</td><td>".join(map(lambda k: "%s,%s" % (sv[k].label, sv[k].units), sv))
			r += "</td></tr><tr><td>"
			r += "</td><td>".join(map(lambda k: str(sv[k]), sv))
		else:
			r += "N/A"

		r += "</td></tr></table>"

		r += "<h3>%s, ts: %d</h3>" % (self.tstat, tstamp - time())

		if len(self.mlog) > 1:
			r += "<hr>"
			r += plot_svg(self.mlog, 800, 400)
		r += "<body></html>"
		return r

	def http_cmd(self, a):
		global ttemp, log_en

		w = a.split("=")
		if w[0] == "ttemp":
			try:
				ttemp = float(w[1])
			except:
				pass
		elif w[0] == "log":
			try:
				log_en = int(w[1])
			except:
				pass
		elif w[0] == "threads":
			dump_threads()

	def http_ctl(self, args):
		global log_en
		if args:
			for a in args.split("&"):
				if a:
					self.http_cmd(a)

		r = '<html><body bgcolor="#faf296">'
		r += '''
<form action="/ctl" method="get" style="font-size: 150%">
Thermostat: <input type="number" step="0.1" name=ttemp>
<input type="submit" value="OK">
</form>'''
		r += '<table><tr><td><h3><a href="/sens.csv" target="_blank">Download sensors data</a></h3></td>'
		if log_en:
			r += '''<td>
<form action="/ctl" method="get" style="font-size: 150%">
<input type="hidden" name="log" value="0">
<input type="submit" value="Stop">
</form></td>'''
		else:
			r += '''<td>
<form action="/ctl" method="get" style="font-size: 150%">
<input type="hidden" name="log" value="1">
<input type="submit" value="Overwrite">
</form></td>
<td><form action="/ctl" method="get" style="font-size: 150%">
<input type="hidden" name="log" value="2">
<input type="submit" value="Append">
</form></td>'''
		r += "</tr></table>"

		r += ''
		r += '''<table><tr><td>
<h3><a href="/err" target="_blank">Error log</a></h3></td>
<td><form action="/ctl" method="get" style="font-size: 150%">
<input type="hidden" name="threads" value="1">
<input type="submit" value="Dump threads"></td>
</tr></form>'''
		r += "</body></html>"
		return r

	def html(self, p, c):
		if p == "/sens":
			re = self.http_sens()
		elif p == "/ctl":
			re = self.http_ctl(c)
		elif p == "/err":
			with open(self.cfg["errfn"]) as f:
				re = ("<html><body><pre>" +
					f.read() +
					"</pre></body></html>")
		else:
			re = self.http_frame()

		rh = "HTTP/1.1 200 OK\r\nContent-Length: %d\r\nContent-Type: text/html\r\n\r\n" % len(re)
		self.send(rh + re)

	def send(self, msg):
		try:
			self.con.send(msg)
			return 0
		except:
			return -1

	def sendfile(self, fn, fmt = "text/csv"):
		msz = 4 << 10

		if not fn or not os.path.isfile(fn):
			self.send("HTTP/1.1 404 Not Found\r\n\r\n404 Not Found")
			return
		sz = os.path.getsize(fn)
		if self.send("HTTP/1.1 200 OK\r\nContent-Length: %d\r\nContent-Type: %s\r\n\r\n" % (sz, fmt)):
			return
		with open(fn) as f:
			while 1:
				d = f.read(msz)
				if not d:
					break
				if self.send(d):
					break
	def main(self):
		global log
		sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
		sk.bind(("", 80))
		sk.listen(10)
		sk.settimeout(1)
		while run:
			con = None
			try:
				con, addr = sk.accept()
				con.settimeout(1)
				r = con.recv(1024)
			except:
				if con:
					con.close()
				continue
			self.con = con
			url = None
			for h in r.split("\r\n"):
				w = h.split()
				if len(w) != 3:
					continue
				if w[0] == "GET":
					url = w[1]
			if not url:
				con.close()
				continue
			w = url.split("?")
			p = w[0]
			if p == "/sens.csv":
				if type(log) is file:
					log.flush()
				self.sendfile(self.cfg["logfn"])
			else:
				self.html(p, w[1] if len(w) > 1 else None)
			con.close()
		sk.close()

def main(cfg):
	global ttemp, thyst, heat, tstamp, sv, log_en, log

	sv = None

	tstamp = time()

	lcd = clcd(cfg["lcdw"], cfg["lcdh"]) if cfg["lcd"] else None

	button = gpio1(cfg["button"])

	i2c_rst = gpio1(cfg["i2c_rst_gpio"])
	i2c_rst.set(0)

	sens = {
		"ta" : insysfs(cfg["temp_adt"], 1e-3, "Ta", "C", 2),
		"th" : insysfs(cfg["temp_htu"], 1e-3, "Th", "C", 2),
		"rh" : insysfs(cfg["rh_htu"], 1e-3, "RH", "%", 0),
		"o2" : insysfs(cfg["o2"], 20.8 * 256. / 0x7fff / 11.8, "O2", "%", 1),
		"co2" : t6700(cfg["co2_bus"], cfg["co2_addr"]),
	}

	ttemp = cfg["ttemp"]
	thyst = cfg["thyst"]
	ts = tstat(heat, ttemp, thyst)

	log_en = 0
	log = None
	logk = sens.keys()

	avt = 0
	avn = 0
	avper = cfg["avper"]
	avstart = time()
	ml = []

	http = http_serv(cfg, ts, ml)
	http = Thread(target = http.main)
	http.start()

	wake = time()
	while run:
		tstamp = time()
		sv = dict(map(lambda k: (k, sens[k].read()), sens.keys()))

		if (sv[cfg["tsens"]].val < 0):
			i2c_rst.set(1)
			sleep(0.1)
			i2c_rst.set(0)

		try:
			avt += sv[cfg["tsens"]].val
			avn += 1
			if time() - avstart >= avper:
				avstart += avper
				if avstart < time():
					avstart = time() + avper
				ml.append((time(), avt / avn))
				while len(ml) > cfg["avn"]:
					ml.pop(0)
				avt, avn = 0, 0
		except:
			pass
		if ttemp != ts.temp:
			ts.set(ttemp)
		ts.tstat(sv[cfg["tsens"]].val)
		lcd_upd(lcd, sv)

		if log_en and not log and cfg["logfn"]:
			log = open(cfg["logfn"], "w" if log_en == 1 else "a")
			log_header(log, logk, sens)
		if not log_en and log:
			log.close()
			log = None
		sdump(log, logk, sv)

		wake += 1
		now = time()
		if wake > now:
			sleep(wake - now)
		else:
			wake = now
	wdt.join(1)
	http.join(1)

def parse_cmdline(cfg):
	for arg in sys.argv[1:]:
		r = arg.split("=")
		k = r[0]
		v = 1
		if len(r) >= 2:
			try:
				v = int(r[1], 0)
			except:
				try:
					v = float(r[1])
				except:
					v = r[1]
		cfg[k] = v
	print cfg
	return cfg

def dump_threads(sig=0, fr=None):
	sys.stderr.write("\n%s: Threads stack\n" % hts())
	for i, t in sys._current_frames().items():
		sys.stderr.write("\n%d\n" % i)
		print_stack(t)

def quit(sig, fr):
	global run, heat
	run = 0
	heat.set(0)
	if sig == signal.SIGTERM:
		sys.stderr.write(hts() + ": SIGTERM\n")

if __name__ == "__main__":
	global heat
	cfg = parse_cmdline(defaults)

	heat = gpio1(cfg["heat"])

	if cfg["detach"]:
		daemonize(cfg["errfn"])

	if cfg["pidfn"]:
		with open(cfg["pidfn"], "w") as f:
			f.write("%d" % os.getpid())

	signal.signal(signal.SIGTERM, quit)
	signal.signal(signal.SIGUSR1, dump_threads)

	try:
		sys.stderr.write(hts() + ": START\n")
		main(cfg)
	finally:
		quit(0, 0)
		sys.stderr.write(hts() + ": STOP\n")