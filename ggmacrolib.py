"""Desktop primitives by Gediz GÜRSU. Requires desktop backend permissions."""
import re
import numpy as np
from PIL import Image, ImageGrab
import cv2
import time
from pynput.mouse import Listener
from pynput.mouse import Button
import matplotlib.pyplot as plt
import pynput
import math
import pytesseract
import random
import os
import sys
import importlib


from ggmacro.runtime import MacroLoom, SupervisedMacroRunner


class DesktopError(RuntimeError):
    """Desktop operation failed; no input should be dispatched on this basis."""


class UnsupportedDesktopError(DesktopError):
    """The current OS or session does not support this operation."""


def _window_backend():
    """Load the optional native window backend without changing OS settings."""
    if sys.platform not in ('win32', 'darwin', 'linux'):
        raise UnsupportedDesktopError(f'Unsupported window platform: {sys.platform}')
    if sys.platform == 'linux':
        if os.environ.get('XDG_SESSION_TYPE', '').lower() == 'wayland' or os.environ.get('WAYLAND_DISPLAY'):
            raise UnsupportedDesktopError('Window management requires X11; native Wayland is not supported')
        if not os.environ.get('DISPLAY'):
            raise UnsupportedDesktopError('Window management requires a graphical desktop (DISPLAY is unset)')
    try:
        return importlib.import_module('pywinctl')
    except ImportError as exc:
        raise DesktopError('Window management requires PyWinCtl: python -m pip install pywinctl') from exc
    except Exception as exc:
        raise DesktopError(f'Cannot connect to window backend; check desktop permissions: {exc}') from exc


def find_windows(title):
    """Return PyWinCtl window objects matching a case-insensitive title substring.

    Windows, macOS and Linux/X11 share this API. An empty list means no match;
    unavailable backends and restricted sessions raise DesktopError instead.
    Inspect each object's title to disambiguate matches before focusing it.
    """
    if not isinstance(title, str) or not title.strip():
        raise ValueError('title must be a nonempty string')
    backend = _window_backend()
    try:
        return backend.getWindowsWithTitle(title, condition=backend.Re.CONTAINS,
                                          flags=backend.Re.IGNORECASE)
    except Exception as exc:
        raise DesktopError(f'Cannot enumerate windows: {exc}') from exc


def is_focused(window):
    """Return whether a previously found window is alive and currently active."""
    if window is None:
        return False
    _window_backend()
    try:
        return bool(window.isAlive and window.isActive)
    except Exception as exc:
        raise DesktopError(f'Cannot check window focus: {exc}') from exc


def focus_window(window, timeout=2.0):
    """Restore and request focus; return True only after verifying active state.

    False means a closed window or focus was not granted before timeout.
    The OS may reject activation. Backend calls themselves are not interruptible.
    """
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError('timeout must be nonnegative and finite')
    if window is None:
        return False
    _window_backend()
    try:
        if not window.isAlive:
            return False
        if window.isMinimized:
            window.restore(wait=False)
        window.activate(wait=False)
        deadline = time.monotonic() + timeout
        while True:
            if is_focused(window):
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not window.isAlive:
                return False
            time.sleep(min(0.05, remaining))
    except DesktopError:
        raise
    except Exception as exc:
        raise DesktopError(f'Cannot focus window: {exc}') from exc



def rescale(coor):
	return [int(coor[0]/1.5),int(coor[1]/1.5)]

	
def hanging_line(point1, point2,steps):
	import numpy as np
	cfun=np.cosh
	#originall cosh
	a = (point2[1] - point1[1])/(cfun(point2[0]) - cfun(point1[0]))
	b = point1[1] - a*cfun(point1[0])
	x = np.linspace(point1[0], point2[0], steps)
	y = a*cfun(x) + b

	return (x,y)
	
def move(pos2,pos1='current',speed=1):
	m=pynput.mouse.Controller()
	pos2=np.array(pos2)
	if pos1=='current':
		pos1=np.array(m.position)
	orgPos=pos2[:]
	if pos1[0]==pos2[0] and pos1[1]==pos2[1]:
		return 0
	if pos1[0]==pos2[0]:
		pos2[0]=pos2[0]+1
	if pos1[1]==pos2[1]:
		pos2[1]=pos2[1]+1
	l=np.sqrt((pos1[0]-pos2[0])**2+(pos1[1]-pos2[1])**2)
	steps=math.ceil(np.sqrt((pos1[0]-pos2[0])**2+(pos1[1]-pos2[1])**2)/2) #4000 pixel in 160 steps ?
	#print(steps)
	a,b=hanging_line(np.array(pos1)/1000,np.array(pos2)/1000,steps)
	
	if speed=='Decreasing':
		speed=1
		for i in range(len(a)):
			speed-=speed/500
			if i%int(speed*10)==0:
				time.sleep(0.01)
			m.position=(a[i]*1000,b[i]*1000)
	elif speed=='Increasing':
		speed=0.5
		for i in range(len(a)):
			speed+=speed/500
			if i%int(speed*10)==0:
				time.sleep(0.01)
			m.position=(a[i]*1000,b[i]*1000)
	else:
		for i in range(len(a)):
			if i%int(speed*10)==0:
				time.sleep(0.01)
			m.position=(a[i]*1000,b[i]*1000)
	
	m.position=(orgPos[0],orgPos[1])
	del(m)

def wonder(box=[0,1280,0,2560],n=10):
	for i in range(n):
		x0, x1, y0, y1 = box
		x = random.randint(x0, x1)
		y = random.randint(y0, y1)
		
		if random.randint(0,1):
			move((x,y),speed='Decreasing')
		else:
			move((x,y),speed='Increasing')


def click(btn='left',hold=0.005):
	m=pynput.mouse.Controller()
	if btn=='left':
		m.press(Button.left)
		time.sleep(hold)
		m.release(Button.left)
	if btn=='right':
		m.press(Button.right)
		time.sleep(hold)
		m.release(Button.right)
	if btn=='middle':
		m.press(Button.middle)
		time.sleep(hold)
		m.release(Button.middle)
	#time.sleep(0.1)
	del(m)

sign = lambda x: math.copysign(1, x)
def scroll(dy,dt=0.1):
	m=pynput.mouse.Controller()
	for i in range(abs(dy)):
		m.scroll(0,sign(dy))
		time.sleep(dt)

def movePressed(pos2, pos1="current", btn="left", speed=1):
	m=pynput.mouse.Controller()
	if btn=='left':
		temp=pynput.mouse.Button.left
	if btn=='middle':
		temp=pynput.mouse.Button.middle
	if btn=='right':
		temp=pynput.mouse.Button.right
	m.press(temp)
	if pos1=='current':
		pos1=np.array(m.position)
	orgPos=pos2[:]
	if pos1[0]==pos2[0] and pos1[1]==pos2[1]:
		return 0
	if pos1[0]==pos2[0]:
		pos2[0]=pos2[0]+1
	if pos1[1]==pos2[1]:
		pos2[1]=pos2[1]+1
	l=np.sqrt((pos1[0]-pos2[0])**2+(pos1[1]-pos2[1])**2)
	steps=math.ceil(np.sqrt((pos1[0]-pos2[0])**2+(pos1[1]-pos2[1])**2)/2) #4000 pixel in 160 steps ?
	#print(steps)
	a,b=hanging_line(np.array(pos1)/1000,np.array(pos2)/1000,steps)
	for i in range(len(a)):
		if i%int(speed*10)==0:
			time.sleep(0.01)
		m.position=(a[i]*1000,b[i]*1000)
	m.position=(orgPos[0],orgPos[1])
	m.release(temp)
	del(m)

def typeString(x,delay=0.02):
	k=pynput.keyboard.Controller()
	for i in x:
		k.press(i)
		time.sleep(0.01+random.randint(0,50)/1000)
		k.release(i)
		time.sleep(0.01+random.randint(0,50)/1000)
		time.sleep(delay)
	del(k)

def typeEnter(delay=0.02):
	k=pynput.keyboard.Controller()
	k.press(pynput.keyboard.Key.enter)
	time.sleep(0.01+random.randint(0,50)/1000)
	k.release(pynput.keyboard.Key.enter)
	time.sleep(0.01+random.randint(0,50)/1000)
	del(k)
	

def typeHuman(x,delay=0.15):
	k=pynput.keyboard.Controller()
	for i in x:
		k.press(i)
		time.sleep(0.01+random.randint(0,50)/1000)
		k.release(i)
		time.sleep(0.01+random.randint(0,50)/1000)
		time.sleep(random.uniform(delay/2,2*delay))
	del(k)

def typeStringFast(x,delay=0.001):
    k=pynput.keyboard.Controller()
    for i in x:
        k.press(i)
        k.release(i)
    time.sleep(delay)
    del(k)

def getPixPos():
	m=pynput.mouse.Controller()
	s=m.position
	return [s[0],s[1]]

def rightClick():
	click(btn='right')
def middleClick():
	click(btn='middle')
def leftClick():
	click(btn='left')
def presskey(a,dur):
	k=pynput.keyboard.Controller()
	k.press(a)
	try:
		time.sleep(dur)
	finally:
		k.release(a)
	del(k)
import threading

def combine(funcs,arrs):
	threads=[]
	for i in range(len(funcs)):
		threads.append(threading.Thread(target=funcs[i],args=(arrs[i],)))
	for i in threads:
		i.start()

def findPic(n,screen='default'):
	method = cv2.TM_SQDIFF_NORMED
	small_image = cv2.imread(n)
	if screen=='default':
		screen=cv2.cvtColor(np.array(ImageGrab.grab().convert('RGB')),cv2.COLOR_BGR2RGB)
		#cv2.imwrite('screen.png',screen)
		#large_image = cv2.imread('screen.png')
		large_image=screen
	else :
		large_image=screen

	result = cv2.matchTemplate(small_image, large_image, method)
	mn,_,mnLoc,_ = cv2.minMaxLoc(result)
	MPx,MPy = mnLoc
	trows,tcols = small_image.shape[:2]

	return MPx,MPy,tcols,trows,mn


def isInAnyPic(n,screen='default'): #n is array of pictures
	if screen=='default':
		screen=cv2.cvtColor(np.array(ImageGrab.grab().convert('RGB')),cv2.COLOR_BGR2RGB)
		cv2.imwrite('screen.png',screen)
		large_image = cv2.imread('screen.png')
	else :
		large_image=screen

	ret=[]
	method = cv2.TM_SQDIFF_NORMED
	for i in n:
		
		small_image = cv2.imread(i)
	
		result = cv2.matchTemplate(small_image, large_image, method)
		mn,_,mnLoc,_ = cv2.minMaxLoc(result)
		if mn<0.05:
			ret.append(True)
		else:
			ret.append(False)
	
	return ret


def findAllPic(n,screen='default'):
	method = cv2.TM_SQDIFF_NORMED
	small_image = cv2.imread(n)
	if screen=='default':
		screen=cv2.cvtColor(np.array(ImageGrab.grab().convert('RGB')),cv2.COLOR_BGR2RGB)
		#cv2.imwrite('screen.png',screen)
		#large_image = cv2.imread('screen.png')
		large_image=screen
	else :
		large_image=screen
	
	mn=0
	ret=[]
	trows,tcols = small_image.shape[:2]
	pnum=0
	while(mn<0.055):
		result = cv2.matchTemplate(small_image, large_image, method)
		mn,_,mnLoc,_ = cv2.minMaxLoc(result)
		#print(cv2.minMaxLoc(result))
		MPx,MPy = mnLoc
		
		if mn<0.05:
			ret.append([MPx,MPy,tcols,trows,mn])
			large_image[MPy:MPy+trows,MPx:MPx+tcols]=[0,0,0]
			#cv2.imwrite('temp'+str(pnum)+'.PNG',large_image)
			pnum+=1
	if len(ret)==1:
		return ret[0]
	else:
		return ret
	
def isShowingPic(pic):
	if findPic(pic)[4]<0.05:
		return True
	else: 
		return False
	
def move2Pic(pic,tol=0.1):
	s=findPic(pic)
	if s[4]<tol :
		move([s[0]+s[2]//2,s[1]+s[3]//2])
	else :
		#print('cannot find even a deformed image')
		return -1

def click2Pic(pic):
	move2Pic(pic)
	time.sleep(0.25)
	click()

def screenRecord(x1,y1,x2,y2): 
	last_time = time.time()
	while(True):
		# 800x600 windowed mode
		printscreen =  np.array(ImageGrab.grab(bbox=(x1,y1,x2,y2)))
		print('loop took {} seconds'.format(time.time()-last_time))
		last_time = time.time()
		cv2.imshow('window',cv2.cvtColor(printscreen, cv2.COLOR_BGR2RGB))
		if cv2.waitKey(25) & 0xFF == ord('q'):
			cv2.destroyAllWindows()
			break
 
def showPixels(x1,y1,x2,y2): 
	plt.imshow(getPixelData(x1,y1,x2,y2))
	plt.show()

def screenshot(region=None, path=None, backend='pillow', capture=None):
	"""Return a PIL image of the screen or a region, optionally saving it.

	region is (left, top, right, bottom) in desktop pixel coordinates.
	Example: screenshot(region=(1920, 0, 3840, 2160), path='right.png')
	backend='mss' enables optional fast capture, also returning a PIL RGB image.
	Pass a reusable MSS object as capture= inside the owning worker for loops.
	Without region, MSS captures the combined desktop across all monitors.
	"""
	if backend not in ('pillow', 'mss'):
		raise ValueError("backend must be 'pillow' or 'mss'")
	if capture is not None and backend != 'mss':
		raise ValueError("capture requires backend='mss'")
	if region is not None:
		if len(region) != 4 or any(not isinstance(v, int) for v in region):
			raise ValueError('region must contain four integer coordinates')
		left, top, right, bottom = region
		if right <= left or bottom <= top:
			raise ValueError('region must have positive width and height')
		region = tuple(region)
	if backend == 'mss':
		def grab(sct):
			monitor = sct.monitors[0] if region is None else {
				'left': left, 'top': top, 'width': right-left, 'height': bottom-top}
			raw = sct.grab(monitor)
			return Image.frombytes('RGB', raw.size, raw.bgra, 'raw', 'BGRX')
		if capture is not None:
			image = grab(capture)
		else:
			try:
				mss = importlib.import_module('mss')
			except ImportError as exc:
				raise DesktopError('Fast capture requires MSS: python -m pip install mss') from exc
			with mss.MSS() as sct:
				image = grab(sct)
	else:
		image = ImageGrab.grab(bbox=region)
	if path is not None:
		image.save(path)
	return image


def chooseAction(decider, state, actions, goal, region=None):
	"""Ask an action selector; this does not execute its answer.

	Pass a desktop region to include a fresh screenshot, or omit it to
	use structured state only. actions maps allowed names to descriptions.
	"""
	import io
	observed_at = time.monotonic()
	image = None
	if region is not None:
		buffer = io.BytesIO()
		screenshot(region=region).save(buffer, format='PNG')
		image = buffer.getvalue()
	return decider.choose(state, actions, goal, image=image,
						  observed_at=observed_at)


def getPixelData(x1,y1,x2,y2): 
	return np.array(ImageGrab.grab(bbox=(x1,y1,x2,y2)))
		
def viewImage(img):
	plt.imshow(img)
	plt.show()
	
def markRect(img,x1,y1,x2,y2):
	img[y1,x1:x2]=[0,255,0]
	img[y2,x1:x2]=[0,255,0]
	img[y1:y2,x1]=[0,255,0]
	img[y1:y2,x2]=[0,255,0]
		

def tessy_prot(s1,s2):
		cap = ImageGrab.grab(bbox =(s1[0], s1[1], s2[0],s2[1]))
		tesstr = pytesseract.image_to_string(
				cv2.cvtColor(np.array(cap), cv2.COLOR_BGR2GRAY),
				lang ='eng')
		print(tesstr)


def eraseFromImage(n,screen='default'):
	method = cv2.TM_SQDIFF_NORMED
	small_image = cv2.imread(n)
	if screen=='default':
		screen=cv2.cvtColor(np.array(ImageGrab.grab().convert('RGB')),cv2.COLOR_BGR2RGB)
		#cv2.imwrite('screen.png',screen)
		#large_image = cv2.imread('screen.png')
		large_image=screen
	else :
		large_image=screen
	
	mn=0
	ret=[]
	trows,tcols = small_image.shape[:2]
	pnum=0
	while(mn<0.05):
		result = cv2.matchTemplate(small_image, large_image, method)
		mn,_,mnLoc,_ = cv2.minMaxLoc(result)
		#print(cv2.minMaxLoc(result))
		MPx,MPy = mnLoc
		
		if mn<0.05:
			ret.append([MPx,MPy,tcols,trows,mn])
			large_image[MPy:MPy+trows,MPx:MPx+tcols]=[0,0,0]
			#cv2.imwrite('temp'+str(pnum)+'.PNG',large_image)
			pnum+=1
	return large_image
	
	# The image is only displayed if we call thiss=ImageGrab.grab(bbox =(s1[0], s1[1], s2[0],s2[1]))


#
#f=open('mycro.txt')
#temptoeval=f.read()
#f.close()
#exec(temptoeval)

