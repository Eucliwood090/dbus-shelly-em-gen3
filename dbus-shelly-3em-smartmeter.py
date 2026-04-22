#!/usr/bin/env python
# vim: ts=2 sw=2 et

import platform 
import logging
import logging.handlers
import sys
import os
import time
import requests
import configparser
 
if sys.version_info.major == 2:
    import gobject
else:
    from gi.repository import GLib as gobject

sys.path.insert(1, os.path.join(os.path.dirname(__file__), '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python'))
from vedbus import VeDbusService

class DbusShelly3emService:
  def __init__(self, paths, productname='Shelly EM Gen3', connection='Shelly EM Gen3 RPC service'):
    config = self._getConfig()
    deviceinstance = int(config['DEFAULT']['DeviceInstance'])
    customname = config['DEFAULT']['CustomName']
    role = config['DEFAULT']['Role']

    allowed_roles = ['pvinverter','grid']
    if role in allowed_roles:
        servicename = 'com.victronenergy.' + role
    else:
        logging.error("Configured Role: %s is not in the allowed list" % role)
        exit()

    productid = 0xA144 if role == 'pvinverter' else 45069

    self._dbusservice = VeDbusService("{}.http_{:02d}".format(servicename, deviceinstance))
    self._paths = paths
 
    self._dbusservice.add_path('/Mgmt/ProcessName', __file__)
    self._dbusservice.add_path('/Mgmt/ProcessVersion', '1.0 Gen3-RPC on Python ' + platform.python_version())
    self._dbusservice.add_path('/Mgmt/Connection', connection)
    self._dbusservice.add_path('/DeviceInstance', deviceinstance)
    self._dbusservice.add_path('/ProductId', productid)
    self._dbusservice.add_path('/DeviceType', 345)
    self._dbusservice.add_path('/ProductName', productname)
    self._dbusservice.add_path('/CustomName', customname)
    self._dbusservice.add_path('/Latency', None)
    self._dbusservice.add_path('/FirmwareVersion', 0.3)
    self._dbusservice.add_path('/HardwareVersion', 0)
    self._dbusservice.add_path('/Connected', 1)
    self._dbusservice.add_path('/Role', role)
    self._dbusservice.add_path('/Position', self._getShellyPosition())
    self._dbusservice.add_path('/Serial', self._getShellySerial())
    self._dbusservice.add_path('/UpdateIndex', 0)
 
    for path, settings in self._paths.items():
      self._dbusservice.add_path(
        path, settings['initial'], gettextcallback=settings['textformat'], writeable=True, onchangecallback=self._handlechangedvalue)
 
    self._lastUpdate = 0
    gobject.timeout_add(500, self._update)
    gobject.timeout_add(self._getSignOfLifeInterval()*60*1000, self._signOfLife)
 
  def _getShellySerial(self):
    meter_data = self._getShellyData()  
    return meter_data['sys']['mac']
 
  def _getConfig(self):
    config = configparser.ConfigParser()
    config.read("%s/config.ini" % (os.path.dirname(os.path.realpath(__file__))))
    return config
 
  def _getSignOfLifeInterval(self):
    config = self._getConfig()
    value = config['DEFAULT'].get('SignOfLifeLog', 5)
    return int(value)
 
  def _getShellyPosition(self):
    config = self._getConfig()
    value = config['DEFAULT'].get('Position', 0)
    return int(value)
 
  def _getShellyStatusUrl(self):
    config = self._getConfig()
    # Utilisation de l'API RPC pour Gen3
    URL = "http://%s/rpc/Shelly.GetStatus" % (config['ONPREMISE']['Host'])
    return URL
    
  def _getShellyData(self):
    URL = self._getShellyStatusUrl()
    meter_r = requests.get(url = URL, timeout=5)
    if not meter_r:
        raise ConnectionError("No response from Shelly EM Gen3 - %s" % (URL))
    meter_data = meter_r.json()     
    if not meter_data:
        raise ValueError("Converting response to JSON failed")
    return meter_data
 
  def _signOfLife(self):
    logging.info("--- Start: sign of life ---")
    logging.info("Last '/Ac/Power': %s" % (self._dbusservice['/Ac/Power']))
    return True
 
  def _update(self):   
    try:
      meter_data = self._getShellyData()
      
      # Extraction des données spécifiques au Gen3 (Pince 0 = em1:0)
      pince0 = meter_data['em1:0']
      energy0 = meter_data['em1data:0']
      
      # Mise à jour des valeurs DBus (Monophasé L1)
      power = pince0['act_power']
      self._dbusservice['/Ac/Power'] = power
      self._dbusservice['/Ac/L1/Voltage'] = pince0['voltage']
      self._dbusservice['/Ac/L1/Current'] = pince0['current']
      self._dbusservice['/Ac/L1/Power'] = power
      
      # Énergie (Wh to kWh)
      self._dbusservice['/Ac/L1/Energy/Forward'] = energy0['total_act_energy'] / 1000
      self._dbusservice['/Ac/L1/Energy/Reverse'] = energy0['total_act_ret_energy'] / 1000
      
      # Calcul global
      self._dbusservice['/Ac/Energy/Forward'] = self._dbusservice['/Ac/L1/Energy/Forward']
      self._dbusservice['/Ac/Energy/Reverse'] = self._dbusservice['/Ac/L1/Energy/Reverse']
      
      self._dbusservice['/UpdateIndex'] = (self._dbusservice['/UpdateIndex'] + 1 ) % 256
      self._lastUpdate = time.time()
    except Exception as e:
       logging.critical('Error at %s', '_update', exc_info=e)
       
    return True
 
  def _handlechangedvalue(self, path, value):
    return True

def getLogLevel():
  config = configparser.ConfigParser()
  config.read("%s/config.ini" % (os.path.dirname(os.path.realpath(__file__))))
  logLevelString = config['DEFAULT'].get('LogLevel', 'INFO')
  return logging.getLevelName(logLevelString)

def main():
  logging.basicConfig(format='%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s',
                      datefmt='%Y-%m-%d %H:%M:%S',
                      level=getLogLevel(),
                      handlers=[
                          logging.FileHandler("%s/current.log" % (os.path.dirname(os.path.realpath(__file__)))),
                          logging.StreamHandler()
                      ])
  try:
      logging.info("Start Shelly EM Gen3 Service")
      from dbus.mainloop.glib import DBusGMainLoop
      DBusGMainLoop(set_as_default=True)
     
      _kwh = lambda p, v: (str(round(v, 2)) + ' kWh')
      _a = lambda p, v: (str(round(v, 1)) + ' A')
      _w = lambda p, v: (str(round(v, 1)) + ' W')
      _v = lambda p, v: (str(round(v, 1)) + ' V')   
     
      pvac_output = DbusShelly3emService(
        paths={
          '/Ac/Energy/Forward': {'initial': 0, 'textformat': _kwh},
          '/Ac/Energy/Reverse': {'initial': 0, 'textformat': _kwh},
          '/Ac/Power': {'initial': 0, 'textformat': _w},
          '/Ac/L1/Voltage': {'initial': 0, 'textformat': _v},
          '/Ac/L1/Current': {'initial': 0, 'textformat': _a},
          '/Ac/L1/Power': {'initial': 0, 'textformat': _w},
          '/Ac/L1/Energy/Forward': {'initial': 0, 'textformat': _kwh},
          '/Ac/L1/Energy/Reverse': {'initial': 0, 'textformat': _kwh},
        })
     
      mainloop = gobject.MainLoop()
      mainloop.run()            
  except Exception as e:
    logging.critical('Error at %s', 'main', exc_info=e)

if __name__ == "__main__":
  main()
