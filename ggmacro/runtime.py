"""Portable macro scheduling; no desktop or model dependencies."""
import math
import threading
import time


class MacroLoom:
	"""Thread-safe continuous real-time macro runner ("loom").

	Executes high-frequency repetitive macro routines (e.g., keeping character
	moving, corridor patrol, low-latency keypresses) on a background worker thread.
	Supports dynamic parameter modification and hot-swapping of routines on the fly
	while running, without interrupting thread execution.
	"""

	def __init__(self, routine=None, interval=0.08, name='MacroLoom', check=None):
		"""Initialize the loom with an optional routine and step interval in seconds."""
		self.routine = routine or (lambda loom: time.sleep(0.01))
		self.interval = float(interval)
		if not math.isfinite(self.interval) or self.interval <= 0:
			raise ValueError('interval must be positive and finite')
		self.check = check or (lambda: True)
		self.last_error = None
		self.name = name
		self._lock = threading.Lock()
		self._stop_event = threading.Event()
		self._pause_event = threading.Event()
		self._thread = None
		self.params = {}
		self.iteration_count = 0
		self.last_run_time = 0.0

	def set_routine(self, routine):
		"""Hot-swap the active macro routine without stopping the worker thread."""
		with self._lock:
			self.routine = routine

	def update_params(self, **kwargs):
		"""Atomically update execution parameters accessible by the active routine."""
		with self._lock:
			self.params.update(kwargs)

	def get_param(self, key, default=None):
		"""Retrieve a parameter value safely under thread lock."""
		with self._lock:
			return self.params.get(key, default)

	def _worker_loop(self):
		"""Internal execution loop running at the configured frequency."""
		while not self._stop_event.is_set():
			if self._pause_event.is_set():
				time.sleep(0.05)
				continue

			start_time = time.monotonic()
			with self._lock:
				active_routine = self.routine

			try:
				if not self.check():
					self.pause()
					continue
				if active_routine is not None:
					active_routine(self)
			except Exception as exc:
				self.last_error = exc
				self._stop_event.set()
				break

			self.iteration_count += 1
			self.last_run_time = time.monotonic()

			elapsed = time.monotonic() - start_time
			sleep_time = self.interval - elapsed
			if sleep_time > 0:
				if self._stop_event.wait(timeout=sleep_time):
					break

	def start(self, paused=False):
		"""Start the background execution thread."""
		if self._thread is not None and self._thread.is_alive():
			return
		self._stop_event.clear()
		self._pause_event.clear()
		if paused:
			self._pause_event.set()
		self.last_error = None
		self._thread = threading.Thread(target=self._worker_loop, name=self.name, daemon=True)
		self._thread.start()

	def pause(self):
		"""Temporarily suspend routine execution."""
		self._pause_event.set()

	def resume(self):
		"""Resume suspended routine execution."""
		self._pause_event.clear()

	def stop(self, timeout=2.0):
		"""Signal the worker loop to terminate and await thread shutdown."""
		self._stop_event.set()
		if self._thread is not None and self._thread.is_alive():
			self._thread.join(timeout=timeout)

	@property
	def is_running(self):
		"""Return True if worker thread is alive and not stopped."""
		return self._thread is not None and self._thread.is_alive() and not self._stop_event.is_set()


class SupervisedMacroRunner:
	"""Coordinates a high-frequency real-time MacroLoom with a 2-3 second supervisory loop.

	The MacroLoom handles sub-second responsive macros (key pulses, steering).
	The supervisor evaluates situation every supervisor_interval seconds (e.g. 2.0-3.0s),
	inspects screen/game state, consults the local AI decider or manual rules,
	and dynamically adjusts parameters or hot-swaps routines on the running loom.
	"""

	def __init__(self, loom, supervisor_fn, supervisor_interval=2.5, focus_check=None):
		"""Initialize runner with a loom, supervisor callback, and cycle interval."""
		self.loom = loom
		self.supervisor_fn = supervisor_fn
		self.supervisor_interval = float(supervisor_interval)
		if not math.isfinite(self.supervisor_interval) or self.supervisor_interval <= 0:
			raise ValueError('supervisor_interval must be positive and finite')
		self.last_error = None
		self.focus_check = focus_check or (lambda: True)
		self._stop_event = threading.Event()
		self._thread = None
		self.supervision_count = 0
		self.last_supervision_time = 0.0

	def _supervisory_loop(self):
		"""Background supervisory worker waking up every 2-3 seconds."""
		while not self._stop_event.is_set():
			# Verify application focus and safety invariants
			try:
				permitted = self.focus_check()
			except Exception as exc:
				self.last_error = exc
				self.loom.stop()
				self._stop_event.set()
				break
			if self.loom.last_error is not None:
				self.last_error = self.loom.last_error
				self._stop_event.set()
				break
			if not permitted:
				print(f"[SupervisedMacroRunner] Focus lost or check failed; pausing loom.")
				self.loom.pause()
				if self._stop_event.wait(timeout=0.5):
					break
				continue
			else:
				if not self.loom.is_running:
					self.loom.start()
				else:
					self.loom.resume()

			cycle_start = time.monotonic()
			try:
				# Invoke supervisory evaluation and adjustment logic
				self.supervisor_fn(self)
				self.supervision_count += 1
				self.last_supervision_time = time.monotonic()
			except Exception as exc:
				self.last_error = exc
				self.loom.stop()
				self._stop_event.set()
				break

			# Compute remaining sleep for next supervisory check (default 2-3s)
			elapsed = time.monotonic() - cycle_start
			sleep_time = max(0.1, self.supervisor_interval - elapsed)
			if self._stop_event.wait(timeout=sleep_time):
				break

	def start(self):
		"""Start both loops, allowing input only after the first focus check."""
		if self._thread is not None and self._thread.is_alive():
			return
		self.last_error = None
		self._stop_event.clear()
		self.loom.start(paused=True)
		self._thread = threading.Thread(target=self._supervisory_loop, name='SupervisorWorker', daemon=True)
		self._thread.start()

	def stop(self, timeout=2.0):
		"""Stop supervisory loop and associated macro loom cleanly."""
		self._stop_event.set()
		self.loom.stop(timeout=timeout)
		if self._thread is not None and self._thread.is_alive():
			self._thread.join(timeout=timeout)



