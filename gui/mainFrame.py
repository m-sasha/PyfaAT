# =============================================================================
# Copyright (C) 2010 Diego Duclos
#
# This file is part of pyfa.
#
# pyfa is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# pyfa is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with pyfa.  If not, see <http://www.gnu.org/licenses/>.
# =============================================================================

import datetime
import os.path
import threading
import time
import webbrowser
from codecs import open
from time import gmtime, strftime
from typing import Optional, Any
from operator import itemgetter

from logbook import Logger

# noinspection PyPackageRequirements
import wx
import wx.adv
from logbook import Logger
# noinspection PyPackageRequirements
# noinspection PyPackageRequirements
from wx.lib.inspection import InspectionTool

import config

from eos.config import gamedata_version, gamedata_date
import datetime

import urllib.request
import json

import gui.aboutData
from gui.chrome_tabs import ChromeNotebook
import gui.globalEvents as GE
from eos.config import gamedata_date, gamedata_version
from eos.db.saveddata.loadDefaultDatabaseValues import DefaultDatabaseValues
from eos.db.saveddata.queries import getFit as db_getFit
# import this to access override setting
from eos.modifiedAttributeDict import ModifiedAttributeDict
from gui import graphFrame
from gui.additionsPane import AdditionsPane
from gui.bitmap_loader import BitmapLoader
from gui.builtinMarketBrowser.events import ItemSelected
from gui.builtinShipBrowser.events import FitSelected, ImportSelected, Stage3Selected
# noinspection PyUnresolvedReferences
from gui.builtinViews import emptyView, entityEditor, fittingView, implantEditor  # noqa: F401
from gui.characterEditor import CharacterEditor
from gui.characterSelection import CharacterSelection
from gui.chrome_tabs import ChromeNotebook
from gui.copySelectDialog import CopySelectDialog
from gui.devTools import DevTools
from gui.esiFittings import EveFittings, ExportToEve, SsoCharacterMgmt
from gui.graphFrame import GraphFrame
from gui.mainMenuBar import MainMenuBar
from gui.marketBrowser import MarketBrowser
from gui.multiSwitch import MultiSwitch
from gui.patternEditor import DmgPatternEditorDlg
from gui.preferenceDialog import PreferenceDialog
from gui.resistsEditor import ResistsEditorDlg
from gui.setEditor import ImplantSetEditorDlg
from gui.shipBrowser import ShipBrowser
from gui.ssoLogin import SsoLogin
from gui.statsPane import StatsPane
from gui.updateDialog import UpdateDialog
from gui.utils.clipboard import fromClipboard, toClipboard
# noinspection PyUnresolvedReferences
from gui.builtinViews import emptyView, entityEditor, fittingView, implantEditor  # noqa: F401
from gui.utils.exportStats import statsExportText
from gui import graphFrame

from service.settings import SettingsProvider
from service.fit import Fit
from service.character import Character
from service.esi import Esi, LoginMethod
from service.esiAccess import SsoMode
from service.fit import Fit
from service.port import EfsPort, IPortUser, Port
from service.settings import HTMLExportSettings, SettingsProvider
from service.update import Update

# import this to access override setting
from eos.modifiedAttributeDict import ModifiedAttributeDict
from eos.db.saveddata.loadDefaultDatabaseValues import DefaultDatabaseValues
from eos.db.saveddata.queries import getFit as db_getFit
from service.port import Port, IPortUser
from service.settings import HTMLExportSettings

from time import gmtime, strftime

import threading
import webbrowser
import wx.adv
import service.esiapi as esiapi

from service.esi import Esi, LoginMethod
from gui.esiFittings import EveFittings, ExportToEve, SsoCharacterMgmt

from at.setupsFrame import SetupsFrame

disableOverrideEditor = False

try:
    from gui.propertyEditor import AttributeEditor
except ImportError as e:
    AttributeEditor = None
    print(("Error loading Attribute Editor: %s.\nAccess to Attribute Editor is disabled." % e.message))
    disableOverrideEditor = True

pyfalog = Logger(__name__)

pyfalog.debug("Done loading mainframe imports")


# dummy panel(no paint no erasebk)
class PFPanel(wx.Panel):
    def __init__(self, parent):
        wx.Panel.__init__(self, parent)
        self.Bind(wx.EVT_PAINT, self.OnPaint)
        self.Bind(wx.EVT_ERASE_BACKGROUND, self.OnBkErase)

    def OnPaint(self, event):
        event.Skip()

    def OnBkErase(self, event):
        pass


class OpenFitsThread(threading.Thread):
    def __init__(self, fits, callback):
        threading.Thread.__init__(self)
        self.name = "LoadingOpenFits"
        self.mainFrame = MainFrame.getInstance()
        self.callback = callback
        self.fits = fits
        self.start()

    def run(self):
        time.sleep(0.5)  # Give GUI some time to finish drawing

        # `startup` tells FitSpawner that we are loading fits are startup, and
        # has 3 values:
        # False = Set as default in FitSpawner itself, never set here
        # 1 = Create new fit page, but do not calculate page
        # 2 = Create new page and calculate
        # We use 1 for all fits except the last one where we use 2 so that we
        # have correct calculations displayed at startup
        for fitID in self.fits[:-1]:
            wx.PostEvent(self.mainFrame, FitSelected(fitID=fitID, startup=1))

        wx.PostEvent(self.mainFrame, FitSelected(fitID=self.fits[-1], startup=2))
        wx.CallAfter(self.callback)


# Example URL: https://zkillboard.com/api/regionID/10000004/year/2018/month/08/allianceID/1614483120/
class ImportFromZKillboardThread(threading.Thread):
    def __init__(self, url, progressCallback, doneCallback):
        threading.Thread.__init__(self)
        self.name = "ImportingFromZKillboard"
        self.progressCallback = progressCallback
        self.doneCallback = doneCallback
        self.url = url
        self.start()

    def run(self):
        with urllib.request.urlopen(self.url) as response:
            wx.CallAfter(lambda: self.progressCallback("Loading killmails"))
            km_ids_and_hashes = [(km["killmail_id"], km["zkb"]["hash"]) for km in json.loads(response.read())]
            killmails = []
            for count, (km_id,km_hash) in enumerate(km_ids_and_hashes):
                wx.CallAfter(lambda: self.progressCallback("Loading killmail %d/%d" % (count+1, len(km_ids_and_hashes))))
                killmails.append(esiapi.fetchKillmail(km_id, km_hash))
            killmails.sort(key=lambda km: km["killmail_time"])

            from datetime import datetime
            TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

            # System ids to the fights that occurred in them, in time order
            # Fight is a ({ticker1, ticker2}, list[(killmail, fit)])
            from collections import defaultdict
            fightsBySystem = defaultdict(list)
            try:
                for count,killmail in enumerate(killmails):
                    # Import the fit
                    fit = Port.importKillmail(killmail, ImportFromZKillboardThread.chooseFitName)

                    # Group the killmails by fight
                    systemId = killmail["solar_system_id"]
                    fightsInSystem = fightsBySystem[systemId]
                    victimTicker = self.victimTicker(killmail)
                    attackerTicker = self.attackerTicker(killmail)
                    lastFightInSystem = fightsInSystem[-1] if (len(fightsInSystem) > 0) else None
                    lastKillmailTime = datetime.strptime(lastFightInSystem[1][-1][0]["killmail_time"], TIME_FORMAT) if lastFightInSystem is not None else None
                    thisKillmailTime = datetime.strptime(killmail["killmail_time"], TIME_FORMAT)
                    if (lastFightInSystem is None) or (lastFightInSystem[0] != {victimTicker, attackerTicker}) or \
                            (thisKillmailTime - lastKillmailTime).total_seconds() > 60*10:
                        lastFightInSystem = ({victimTicker, attackerTicker}, list())
                        fightsInSystem.append(lastFightInSystem)
                    lastFightInSystem[1].append((killmail, fit))

                    wx.CallAfter(lambda: self.progressCallback("Processed %d killmails" % (count+1)))

                # Import the setups
                import itertools
                from at.setup import Setup, SetupShip, StoredSetups
                fights = list(itertools.chain.from_iterable(fightsBySystem.values()))
                for fight in fights:
                    for ticker1 in fight[0]:
                        ticker2 = next(iter(fight[0] - {ticker1}))
                        setup = Setup("%s vs %s" % (ticker1, ticker2))
                        fits = []

                        # Add ships who died
                        addedCharacters = set()
                        for killmail, fit in fight[1]:
                            if self.victimTicker(killmail) == ticker1:
                                setup.ships.append(SetupShip.fromFit(fit))
                                addedCharacters.add(killmail["victim"]["character_id"])
                                fits.append(fit)

                        # Add ships which didn't die
                        for killmail, fit in fight[1]:
                            if self.victimTicker(killmail) != ticker1:
                                for attacker in killmail["attackers"]:
                                    attackerId = attacker["character_id"]
                                    if (attackerId not in addedCharacters) and self.charTicker(attacker) == ticker1:
                                        setup.ships.append(SetupShip(attacker["ship_type_id"]))
                                        addedCharacters.add(attackerId)

                        # Project all fits onto each other
                        # TODO: Only project logistics and command ships onto everyone
                        # import eos.db.saveddata
                        # for fit1 in fits:
                        #     for fit2 in fits:
                        #         if fit1 != fit2:
                        #             fit1.projectedFitDict[fit2.ID] = fit2
                        #             eos.db.saveddata_session.flush()
                        #             eos.db.saveddata_session.refresh(fit2)
                        # eos.db.commit()

                        StoredSetups.addSetup(setup)

            except Exception as ex:
                errorMsg = str(ex)
                wx.CallAfter(lambda: self.doneCallback("Error processing killmail %d: %s" % (killmail["killmail_id"], errorMsg)))
                return

            wx.PostEvent(MainFrame.getInstance(), GE.SetupsChanged())
            wx.CallAfter(lambda: self.doneCallback("Processed %d killmails in %d fights" % (count+1, len(fights))))


    @staticmethod
    def chooseFitName(killmail, fit):
        victimTicker = ImportFromZKillboardThread.victimTicker(killmail)
        attackerTicker = ImportFromZKillboardThread.attackerTicker(killmail)
        return "%s vs %s" % (victimTicker, attackerTicker)


    @staticmethod
    def victimTicker(killmail):
        character = killmail["victim"]
        return ImportFromZKillboardThread.charTicker(character)


    @staticmethod
    def attackerTicker(killmail):
        attackerId = max(killmail["attackers"], key=itemgetter("damage_done")) # The attacker with the highest damage
        return ImportFromZKillboardThread.charTicker(attackerId)


    @staticmethod
    def charTicker(char):
        allianceId = char.get("alliance_id", None)
        corpId = char.get("corporation_id", None)
        charId = char["character_id"]
        if corpId is None:
            charInfo = esiapi.fetchCharacterInfo(charId)
            return charInfo["name"]
        elif allianceId is None:
            corpInfo = esiapi.fetchCorpInfo(corpId)
            return corpInfo["ticker"]
        else:
            allianceInfo = esiapi.fetchAllianceInfo(allianceId)
            return allianceInfo["ticker"]



# todo: include IPortUser again
class MainFrame(wx.Frame):
    __instance = None

    @classmethod
    def getInstance(cls):
        return cls.__instance if cls.__instance is not None else MainFrame()

    def __init__(self, title="pyfa"):
        pyfalog.debug("Initialize MainFrame")
        self.title = title
        wx.Frame.__init__(self, None, wx.ID_ANY, self.title)
        self.supress_left_up = False

        MainFrame.__instance = self

        # Load stored settings (width/height/maximized..)
        self.LoadMainFrameAttribs()

        self.disableOverrideEditor = disableOverrideEditor

        # Fix for msw (have the frame background color match panel color
        if 'wxMSW' in wx.PlatformInfo:
            self.SetBackgroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_BTNFACE))

        # Load and set the icon for pyfa main window
        i = wx.Icon(BitmapLoader.getBitmap("pyfa", "gui"))
        self.SetIcon(i)

        # Create the layout and windows
        mainSizer = wx.BoxSizer(wx.HORIZONTAL)

        self.browser_fitting_split = wx.SplitterWindow(self, style=wx.SP_LIVE_UPDATE)
        self.fitting_additions_split = wx.SplitterWindow(self.browser_fitting_split, style=wx.SP_LIVE_UPDATE)

        mainSizer.Add(self.browser_fitting_split, 1, wx.EXPAND | wx.LEFT, 2)

        self.fitMultiSwitch = MultiSwitch(self.fitting_additions_split)
        self.additionsPane = AdditionsPane(self.fitting_additions_split)

        self.notebookBrowsers = ChromeNotebook(self.browser_fitting_split, False)

        marketImg = BitmapLoader.getBitmap("market_small", "gui")
        shipBrowserImg = BitmapLoader.getBitmap("ship_small", "gui")

        self.marketBrowser = MarketBrowser(self.notebookBrowsers)
        self.notebookBrowsers.AddPage(self.marketBrowser, "Market", bitmap=marketImg, closeable=False)
        self.marketBrowser.splitter.SetSashPosition(self.marketHeight)

        self.shipBrowser = ShipBrowser(self.notebookBrowsers)
        self.notebookBrowsers.AddPage(self.shipBrowser, "Fittings", bitmap=shipBrowserImg, closeable=False)

        self.notebookBrowsers.SetSelection(1)

        self.browser_fitting_split.SplitVertically(self.notebookBrowsers, self.fitting_additions_split)
        self.browser_fitting_split.SetMinimumPaneSize(204)
        self.browser_fitting_split.SetSashPosition(self.browserWidth)

        self.fitting_additions_split.SplitHorizontally(self.fitMultiSwitch, self.additionsPane, -200)
        self.fitting_additions_split.SetMinimumPaneSize(200)
        self.fitting_additions_split.SetSashPosition(self.fittingHeight)
        # self.fitting_additions_split.SetSashGravity(1.0)

        cstatsSizer = wx.BoxSizer(wx.VERTICAL)
        cstatsSizer.AddSpacer(20)

        self.charSelection = CharacterSelection(self)
        cstatsSizer.Add(self.charSelection, 0, wx.EXPAND)
        cstatsSizer.AddSpacer(12)


        # @todo pheonix: fix all stats stuff
        self.statsPane = StatsPane(self)
        cstatsSizer.Add(self.statsPane, 0, wx.EXPAND)

        mainSizer.Add(cstatsSizer, 0, wx.EXPAND)

        self.SetSizer(mainSizer)

        # Add menu
        self.addPageId = wx.NewId()
        self.closePageId = wx.NewId()

        self.widgetInspectMenuID = wx.NewId()
        self.SetMenuBar(MainMenuBar(self))
        self.registerMenu()

        # Internal vars to keep track of other windows (graphing/stats)
        self.graphFrame = None
        self.statsWnds = []
        self.activeStatsWnd = None

        self.Bind(wx.EVT_CLOSE, self.OnClose)

        # Show ourselves
        self.Show()

        self.LoadPreviousOpenFits()

        # Check for updates
        # self.sUpdate = Update.getInstance()
        # self.sUpdate.CheckUpdate(self.ShowUpdateBox)

        self.Bind(GE.EVT_SSO_LOGIN, self.onSSOLogin)
        self.Bind(GE.EVT_SSO_LOGGING_IN, self.ShowSsoLogin)

    @property
    def command(self) -> wx.CommandProcessor:
        return Fit.getCommandProcessor(self.getActiveFit())

    def ShowSsoLogin(self, event):
        if getattr(event, "login_mode", LoginMethod.SERVER) == LoginMethod.MANUAL and getattr(event, "sso_mode", SsoMode.AUTO) == SsoMode.AUTO:
            dlg = SsoLogin(self)
            if dlg.ShowModal() == wx.ID_OK:
                sEsi = Esi.getInstance()
                # todo: verify that this is a correct SSO Info block
                sEsi.handleLogin({'SSOInfo': [dlg.ssoInfoCtrl.Value.strip()]})

    def ShowUpdateBox(self, release, version):
        dlg = UpdateDialog(self, release, version)
        dlg.ShowModal()

    def LoadPreviousOpenFits(self):
        sFit = Fit.getInstance()

        self.prevOpenFits = SettingsProvider.getInstance().getSettings("pyfaPrevOpenFits",
                                                                       {"enabled": False, "pyfaOpenFits": []})
        fits = self.prevOpenFits['pyfaOpenFits']

        # Remove any fits that cause exception when fetching (non-existent fits)
        for id in fits[:]:
            try:
                fit = sFit.getFit(id, basic=True)
                if fit is None:
                    fits.remove(id)
            except:
                fits.remove(id)

        if not self.prevOpenFits['enabled'] or len(fits) is 0:
            # add blank page if there are no fits to be loaded
            self.fitMultiSwitch.AddPage()
            return

        self.waitDialog = wx.BusyInfo("Loading previous fits...")
        OpenFitsThread(fits, self.closeWaitDialog)

    def LoadMainFrameAttribs(self):
        mainFrameDefaultAttribs = {"wnd_size": (1000, 700),
                                   "wnd_position": None,
                                   "wnd_maximized": False,
                                   "browser_width": 300,
                                   "market_height": 0,
                                   "fitting_height": -200}
        self.mainFrameAttribs = SettingsProvider.getInstance().getSettings("pyfaMainWindowAttribs",
                                                                           mainFrameDefaultAttribs)
        isMaximized = self.mainFrameAttribs["wnd_maximized"]
        if isMaximized:
            size = mainFrameDefaultAttribs["wnd_size"]
        else:
            size = self.mainFrameAttribs["wnd_size"]

        self.SetSize(size)
        self.SetMinSize(mainFrameDefaultAttribs["wnd_size"])

        pos = self.mainFrameAttribs["wnd_position"]
        if pos is not None:
            self.SetPosition(pos)

        if isMaximized:
            self.Maximize()

        self.browserWidth = self.mainFrameAttribs["browser_width"]
        self.marketHeight = self.mainFrameAttribs["market_height"]
        self.fittingHeight = self.mainFrameAttribs["fitting_height"]

    def UpdateMainFrameAttribs(self):
        if self.IsIconized():
            return

        self.mainFrameAttribs["wnd_size"] = self.GetSize()
        self.mainFrameAttribs["wnd_position"] = self.GetPosition()
        self.mainFrameAttribs["wnd_maximized"] = self.IsMaximized()

        self.mainFrameAttribs["browser_width"] = self.notebookBrowsers.GetSize()[0]
        self.mainFrameAttribs["market_height"] = self.marketBrowser.marketView.GetSize()[1]
        self.mainFrameAttribs["fitting_height"] = self.fitting_additions_split.GetSashPosition()

    def SetActiveStatsWindow(self, wnd):
        self.activeStatsWnd = wnd

    def GetActiveStatsWindow(self):
        if self.activeStatsWnd in self.statsWnds:
            return self.activeStatsWnd

        if len(self.statsWnds) > 0:
            return self.statsWnds[len(self.statsWnds) - 1]
        else:
            return None

    def RegisterStatsWindow(self, wnd):
        self.statsWnds.append(wnd)

    def UnregisterStatsWindow(self, wnd):
        self.statsWnds.remove(wnd)

    def getActiveFit(self):
        p = self.fitMultiSwitch.GetSelectedPage()
        m = getattr(p, "getActiveFit", None)
        return m() if m is not None else None

    def getActiveView(self):
        self.fitMultiSwitch.GetSelectedPage()

    def CloseCurrentPage(self, evt):
        ms = self.fitMultiSwitch

        page = ms.GetSelection()
        if page is not None:
            ms.DeletePage(page)

    def OnClose(self, event):
        self.UpdateMainFrameAttribs()

        # save open fits
        self.prevOpenFits['pyfaOpenFits'] = []  # clear old list
        for page in self.fitMultiSwitch._pages:
            m = getattr(page, "getActiveFit", None)
            if m is not None:
                self.prevOpenFits['pyfaOpenFits'].append(m())

        # save all teh settingz
        SettingsProvider.getInstance().saveAll()
        event.Skip()

    def ExitApp(self, event):
        SetupsFrame.getInstance().OnAppExit()
        self.Close()

        event.Skip()

    def ShowAboutBox(self, evt):
        info = wx.adv.AboutDialogInfo()
        info.Name = "pyfa"
        time = datetime.datetime.fromtimestamp(int(gamedata_date)).strftime('%Y-%m-%d %H:%M:%S')
        info.Version = config.getVersion() + '\nEVE Data Version: {} ({})'.format(gamedata_version, time)  # gui.aboutData.versionString
        #
        # try:
        #     import matplotlib
        #     matplotlib_version = matplotlib.__version__
        # except:
        #     matplotlib_version = None
        #
        # info.Description = wordwrap(gui.aboutData.description + "\n\nDevelopers:\n\t" +
        #                             "\n\t".join(gui.aboutData.developers) +
        #                             "\n\nAdditional credits:\n\t" +
        #                             "\n\t".join(gui.aboutData.credits) +
        #                             "\n\nLicenses:\n\t" +
        #                             "\n\t".join(gui.aboutData.licenses) +
        #                             "\n\nEVE Data: \t" + gamedata_version +
        #                             "\nPython: \t\t" + '{}.{}.{}'.format(v.major, v.minor, v.micro) +
        #                             "\nwxPython: \t" + wx.__version__ +
        #                             "\nSQLAlchemy: \t" + sqlalchemy.__version__ +
        #                             "\nmatplotlib: \t {}".format(matplotlib_version if matplotlib_version else "Not Installed"),
        #                             500, wx.ClientDC(self))
        # if "__WXGTK__" in wx.PlatformInfo:
        #     forumUrl = "http://forums.eveonline.com/default.aspx?g=posts&amp;t=466425"
        # else:
        #     forumUrl = "http://forums.eveonline.com/default.aspx?g=posts&t=466425"
        # info.WebSite = (forumUrl, "pyfa thread at EVE Online forum")
        wx.adv.AboutBox(info)

    def showDevTools(self, event):
        DevTools(self)

    def showCharacterEditor(self, event):
        dlg = CharacterEditor(self)
        dlg.Show()

    def showAttrEditor(self, event):
        dlg = AttributeEditor(self)
        dlg.Show()

    def showTargetResistsEditor(self, event):
        ResistsEditorDlg(self)

    def showDamagePatternEditor(self, event):
        dlg = DmgPatternEditorDlg(self)
        dlg.ShowModal()
        try:
            dlg.Destroy()
        except RuntimeError:
            pyfalog.error("Tried to destroy an object that doesn't exist in <showDamagePatternEditor>.")

    def showImplantSetEditor(self, event):
        ImplantSetEditorDlg(self)

    def showExportDialog(self, event):
        """ Export active fit """
        sFit = Fit.getInstance()
        fit = sFit.getFit(self.getActiveFit())
        defaultFile = "%s - %s.xml" % (fit.ship.item.name, fit.name) if fit else None

        dlg = wx.FileDialog(self, "Save Fitting As...",
                            wildcard="EVE XML fitting files (*.xml)|*.xml",
                            style=wx.FD_SAVE,
                            defaultFile=defaultFile)
        if dlg.ShowModal() == wx.ID_OK:
            self.supress_left_up = True
            format_ = dlg.GetFilterIndex()
            path = dlg.GetPath()
            if format_ == 0:
                output = Port.exportXml(None, fit)
                if '.' not in os.path.basename(path):
                    path += ".xml"
            else:
                print(("oops, invalid fit format %d" % format_))
                try:
                    dlg.Destroy()
                except RuntimeError:
                    pyfalog.error("Tried to destroy an object that doesn't exist in <showExportDialog>.")
                return

            with open(path, "w", encoding="utf-8") as openfile:
                openfile.write(output)
                openfile.close()

        try:
            dlg.Destroy()
        except RuntimeError:
            pyfalog.error("Tried to destroy an object that doesn't exist in <showExportDialog>.")

    def showPreferenceDialog(self, event):
        dlg = PreferenceDialog(self)
        dlg.ShowModal()

    @staticmethod
    def goWiki(event):
        webbrowser.open('https://github.com/pyfa-org/Pyfa/wiki')

    @staticmethod
    def goForums(event):
        webbrowser.open('https://forums.eveonline.com/t/27156')

    @staticmethod
    def loadDatabaseDefaults(event):
        # Import values that must exist otherwise Pyfa breaks
        DefaultDatabaseValues.importRequiredDefaults()
        # Import default values for damage profiles
        DefaultDatabaseValues.importDamageProfileDefaults()
        # Import default values for target resist profiles
        DefaultDatabaseValues.importResistProfileDefaults()

    def registerMenu(self):
        menuBar = self.GetMenuBar()
        # Quit
        self.Bind(wx.EVT_MENU, self.ExitApp, id=wx.ID_EXIT)
        # Load Default Database values
        self.Bind(wx.EVT_MENU, self.loadDatabaseDefaults, id=menuBar.importDatabaseDefaultsId)
        # Widgets Inspector
        if config.debug:
            self.Bind(wx.EVT_MENU, self.openWXInspectTool, id=self.widgetInspectMenuID)
            self.Bind(wx.EVT_MENU, self.showDevTools, id=menuBar.devToolsId)
        # About
        self.Bind(wx.EVT_MENU, self.ShowAboutBox, id=wx.ID_ABOUT)
        # Char editor
        self.Bind(wx.EVT_MENU, self.showCharacterEditor, id=menuBar.characterEditorId)
        # Damage pattern editor
        self.Bind(wx.EVT_MENU, self.showDamagePatternEditor, id=menuBar.damagePatternEditorId)
        # Target Resists editor
        self.Bind(wx.EVT_MENU, self.showTargetResistsEditor, id=menuBar.targetResistsEditorId)
        # Implant Set editor
        self.Bind(wx.EVT_MENU, self.showImplantSetEditor, id=menuBar.implantSetEditorId)
        # Import dialog
        self.Bind(wx.EVT_MENU, self.fileImportDialog, id=wx.ID_OPEN)
        # Export dialog
        self.Bind(wx.EVT_MENU, self.showExportDialog, id=wx.ID_SAVEAS)
        # Import from Clipboard
        self.Bind(wx.EVT_MENU, self.importFromClipboard, id=wx.ID_PASTE)
        # Import from ZKillboard
        self.Bind(wx.EVT_MENU, self.importFromZKillboard, id=menuBar.importFitsFromZkillboard)
        # Backup fits
        self.Bind(wx.EVT_MENU, self.backupToXml, id=menuBar.backupFitsId)
        # Export skills needed
        self.Bind(wx.EVT_MENU, self.exportSkillsNeeded, id=menuBar.exportSkillsNeededId)
        # Import character
        self.Bind(wx.EVT_MENU, self.importCharacter, id=menuBar.importCharacterId)
        # Export HTML
        self.Bind(wx.EVT_MENU, self.exportHtml, id=menuBar.exportHtmlId)
        # Preference dialog
        self.Bind(wx.EVT_MENU, self.showPreferenceDialog, id=wx.ID_PREFERENCES)
        # User guide
        self.Bind(wx.EVT_MENU, self.goWiki, id=menuBar.wikiId)

        self.Bind(wx.EVT_MENU, lambda evt: MainFrame.getInstance().command.Undo(), id=wx.ID_UNDO)

        self.Bind(wx.EVT_MENU, lambda evt: MainFrame.getInstance().command.Redo(), id=wx.ID_REDO)
        # EVE Forums
        self.Bind(wx.EVT_MENU, self.goForums, id=menuBar.forumId)
        # Save current character
        self.Bind(wx.EVT_MENU, self.saveChar, id=menuBar.saveCharId)
        # Save current character as another character
        self.Bind(wx.EVT_MENU, self.saveCharAs, id=menuBar.saveCharAsId)
        # Save current character
        self.Bind(wx.EVT_MENU, self.revertChar, id=menuBar.revertCharId)

        # Browse fittings
        self.Bind(wx.EVT_MENU, self.eveFittings, id=menuBar.eveFittingsId)
        # Export to EVE
        self.Bind(wx.EVT_MENU, self.exportToEve, id=menuBar.exportToEveId)
        # Handle SSO event (login/logout/manage characters, depending on mode and current state)
        self.Bind(wx.EVT_MENU, self.ssoHandler, id=menuBar.ssoLoginId)

        # Open attribute editor
        self.Bind(wx.EVT_MENU, self.showAttrEditor, id=menuBar.attrEditorId)
        # Toggle Overrides
        self.Bind(wx.EVT_MENU, self.toggleOverrides, id=menuBar.toggleOverridesId)

        # Clipboard exports
        self.Bind(wx.EVT_MENU, self.exportToClipboard, id=wx.ID_COPY)
        self.Bind(wx.EVT_MENU, self.exportFitStatsToClipboard, id=menuBar.fitStatsToClipboardId)

        # Fitting Restrictions
        self.Bind(wx.EVT_MENU, self.toggleIgnoreRestriction, id=menuBar.toggleIgnoreRestrictionID)

        # Graphs
        self.Bind(wx.EVT_MENU, self.openGraphFrame, id=menuBar.graphFrameId)

        toggleSearchBoxId = wx.NewId()
        toggleShipMarketId = wx.NewId()
        ctabnext = wx.NewId()
        ctabprev = wx.NewId()

        # Close Page
        self.Bind(wx.EVT_MENU, self.CloseCurrentPage, id=self.closePageId)
        self.Bind(wx.EVT_MENU, self.HAddPage, id=self.addPageId)
        self.Bind(wx.EVT_MENU, self.toggleSearchBox, id=toggleSearchBoxId)
        self.Bind(wx.EVT_MENU, self.toggleShipMarket, id=toggleShipMarketId)
        self.Bind(wx.EVT_MENU, self.CTabNext, id=ctabnext)
        self.Bind(wx.EVT_MENU, self.CTabPrev, id=ctabprev)

        actb = [(wx.ACCEL_CTRL, ord('T'), self.addPageId),
                (wx.ACCEL_CMD, ord('T'), self.addPageId),

                (wx.ACCEL_CTRL, ord('F'), toggleSearchBoxId),
                (wx.ACCEL_CMD, ord('F'), toggleSearchBoxId),

                (wx.ACCEL_CTRL, ord("W"), self.closePageId),
                (wx.ACCEL_CTRL, wx.WXK_F4, self.closePageId),
                (wx.ACCEL_CMD, ord("W"), self.closePageId),

                (wx.ACCEL_CTRL, ord(" "), toggleShipMarketId),
                (wx.ACCEL_CMD, ord(" "), toggleShipMarketId),

                # Ctrl+(Shift+)Tab
                (wx.ACCEL_CTRL, wx.WXK_TAB, ctabnext),
                (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, wx.WXK_TAB, ctabprev),
                (wx.ACCEL_CMD, wx.WXK_TAB, ctabnext),
                (wx.ACCEL_CMD | wx.ACCEL_SHIFT, wx.WXK_TAB, ctabprev),

                # Ctrl+Page(Up/Down)
                (wx.ACCEL_CTRL, wx.WXK_PAGEDOWN, ctabnext),
                (wx.ACCEL_CTRL, wx.WXK_PAGEUP, ctabprev),
                (wx.ACCEL_CMD, wx.WXK_PAGEDOWN, ctabnext),
                (wx.ACCEL_CMD, wx.WXK_PAGEUP, ctabprev)
                ]

        # Ctrl/Cmd+# for addition pane selection
        self.additionsSelect = []
        for i in range(0, self.additionsPane.notebook.GetPageCount()):
            self.additionsSelect.append(wx.NewId())
            self.Bind(wx.EVT_MENU, self.AdditionsTabSelect, id=self.additionsSelect[i])
            actb.append((wx.ACCEL_CMD, i + 49, self.additionsSelect[i]))
            actb.append((wx.ACCEL_CTRL, i + 49, self.additionsSelect[i]))

        # Alt+1-9 for market item selection
        self.itemSelect = []
        for i in range(0, 9):
            self.itemSelect.append(wx.NewId())
            self.Bind(wx.EVT_MENU, self.ItemSelect, id=self.itemSelect[i])
            actb.append((wx.ACCEL_ALT, i + 49, self.itemSelect[i]))

        atable = wx.AcceleratorTable(actb)
        self.SetAcceleratorTable(atable)

    def toggleIgnoreRestriction(self, event):

        sFit = Fit.getInstance()
        fitID = self.getActiveFit()
        fit = sFit.getFit(fitID)

        if not fit.ignoreRestrictions:
            dlg = wx.MessageDialog(self, "Are you sure you wish to ignore fitting restrictions for the "
                                         "current fit? This could lead to wildly inaccurate results and possible errors.", "Confirm", wx.YES_NO | wx.ICON_QUESTION)
        else:
            dlg = wx.MessageDialog(self, "Re-enabling fitting restrictions for this fit will also remove any illegal items "
                                         "from the fit. Do you want to continue?", "Confirm", wx.YES_NO | wx.ICON_QUESTION)
        result = dlg.ShowModal() == wx.ID_YES
        dlg.Destroy()
        if result:
            sFit.toggleRestrictionIgnore(fitID)
            wx.PostEvent(self, GE.FitChanged(fitID=fitID))

    def eveFittings(self, event):
        dlg = EveFittings(self)
        dlg.Show()

    def onSSOLogin(self, event):
        menu = self.GetMenuBar()
        menu.Enable(menu.eveFittingsId, True)
        menu.Enable(menu.exportToEveId, True)

    def updateEsiMenus(self, type):
        menu = self.GetMenuBar()
        sEsi = Esi.getInstance()

        menu.SetLabel(menu.ssoLoginId, "Manage Characters")
        enable = len(sEsi.getSsoCharacters()) == 0
        menu.Enable(menu.eveFittingsId, not enable)
        menu.Enable(menu.exportToEveId, not enable)

    def ssoHandler(self, event):
        dlg = SsoCharacterMgmt(self)
        dlg.Show()

    def exportToEve(self, event):
        dlg = ExportToEve(self)
        dlg.Show()

    def toggleOverrides(self, event):
        ModifiedAttributeDict.overrides_enabled = not ModifiedAttributeDict.overrides_enabled
        wx.PostEvent(self, GE.FitChanged(fitID=self.getActiveFit()))
        menu = self.GetMenuBar()
        menu.SetLabel(menu.toggleOverridesId,
                      "Turn Overrides Off" if ModifiedAttributeDict.overrides_enabled else "Turn Overrides On")

    def saveChar(self, event):
        sChr = Character.getInstance()
        charID = self.charSelection.getActiveCharacter()
        sChr.saveCharacter(charID)
        wx.PostEvent(self, GE.CharListUpdated())

    def saveCharAs(self, event):
        charID = self.charSelection.getActiveCharacter()
        CharacterEditor.SaveCharacterAs(self, charID)
        wx.PostEvent(self, GE.CharListUpdated())

    def revertChar(self, event):
        sChr = Character.getInstance()
        charID = self.charSelection.getActiveCharacter()
        sChr.revertCharacter(charID)
        wx.PostEvent(self, GE.CharListUpdated())

    def AdditionsTabSelect(self, event):
        selTab = self.additionsSelect.index(event.GetId())

        if selTab <= self.additionsPane.notebook.GetPageCount():
            self.additionsPane.notebook.SetSelection(selTab)

    def ItemSelect(self, event):
        selItem = self.itemSelect.index(event.GetId())

        activeListing = getattr(self.marketBrowser.itemView, 'active', None)
        if activeListing and selItem < len(activeListing):
            wx.PostEvent(self, ItemSelected(itemID=self.marketBrowser.itemView.active[selItem].ID))

    def CTabNext(self, event):
        self.fitMultiSwitch.NextPage()

    def CTabPrev(self, event):
        self.fitMultiSwitch.PrevPage()

    def HAddPage(self, event):
        self.fitMultiSwitch.AddPage()

    def toggleShipMarket(self, event):
        sel = self.notebookBrowsers.GetSelection()
        self.notebookBrowsers.SetSelection(0 if sel == 1 else 1)

    def toggleSearchBox(self, event):
        sel = self.notebookBrowsers.GetSelection()
        if sel == 1:
            self.shipBrowser.navpanel.ToggleSearchBox()
        else:
            self.marketBrowser.search.Focus()

    def clipboardEft(self, options):
        fit = db_getFit(self.getActiveFit())
        toClipboard(Port.exportEft(fit, options))

    def clipboardEftImps(self, options):
        fit = db_getFit(self.getActiveFit())
        toClipboard(Port.exportEftImps(fit))

    def clipboardDna(self, options):
        fit = db_getFit(self.getActiveFit())
        toClipboard(Port.exportDna(fit))

    def clipboardEsi(self, options):
        fit = db_getFit(self.getActiveFit())
        toClipboard(Port.exportESI(fit))

    def clipboardXml(self, options):
        fit = db_getFit(self.getActiveFit())
        toClipboard(Port.exportXml(None, fit))

    def clipboardMultiBuy(self, options):
        fit = db_getFit(self.getActiveFit())
        toClipboard(Port.exportMultiBuy(fit))

    def clipboardEfs(self, options):
        fit = db_getFit(self.getActiveFit())
        toClipboard(EfsPort.exportEfs(fit, 0))

    def importFromClipboard(self, event):
        clipboard = fromClipboard()
        try:
            fits = Port().importFitFromBuffer(clipboard, self.getActiveFit())
        except:
            pyfalog.error("Attempt to import failed:\n{0}", clipboard)
        else:
            self._openAfterImport(fits)

    def importFromZKillboard(self, event):
        dlg = wx.TextEntryDialog(self, "zkillboard.com API URL", "ZKillboard URL")
        if dlg.ShowModal() != wx.ID_OK:
            return
        url = dlg.GetValue()
        dlg.Destroy()
        self.waitDialog = wx.BusyInfo("Loading killmails from %s" % url)
        ImportFromZKillboardThread(url, self.onImportFromZKillboardUpdate, self.onFinishedImportingFromZKillboard)

    def onImportFromZKillboardUpdate(self, info):
        self.closeWaitDialog()
        self.waitDialog = wx.BusyInfo(info)

    def onFinishedImportingFromZKillboard(self, info):
        self.closeWaitDialog()
        wx.MessageBox(info, "Import finished")

    def exportToClipboard(self, event):
        CopySelectDict = {CopySelectDialog.copyFormatEft: self.clipboardEft,
                          # CopySelectDialog.copyFormatEftImps: self.clipboardEftImps,
                          CopySelectDialog.copyFormatXml: self.clipboardXml,
                          CopySelectDialog.copyFormatDna: self.clipboardDna,
                          CopySelectDialog.copyFormatEsi: self.clipboardEsi,
                          CopySelectDialog.copyFormatMultiBuy: self.clipboardMultiBuy,
                          CopySelectDialog.copyFormatEfs: self.clipboardEfs}
        dlg = CopySelectDialog(self)
        dlg.ShowModal()
        selected = dlg.GetSelected()
        options = dlg.GetOptions()

        settings = SettingsProvider.getInstance().getSettings("pyfaExport")
        settings["format"] = selected
        settings["options"] = options

        CopySelectDict[selected](options)

        try:
            dlg.Destroy()
        except RuntimeError:
            pyfalog.error("Tried to destroy an object that doesn't exist in <exportToClipboard>.")

    def exportFitStatsToClipboard(self, event):
        """ Puts fit stats in textual format into the clipboard"""
        fit = db_getFit(self.getActiveFit())
        if fit:
            toClipboard(statsExportText(fit))

    def exportSkillsNeeded(self, event):
        """ Exports skills needed for active fit and active character """
        sCharacter = Character.getInstance()
        saveDialog = wx.FileDialog(
            self,
            "Export Skills Needed As...",
            wildcard=("EVEMon skills training file (*.emp)|*.emp|"
                      "EVEMon skills training XML file (*.xml)|*.xml|"
                      "Text skills training file (*.txt)|*.txt"),
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        )

        if saveDialog.ShowModal() == wx.ID_OK:
            saveFmtInt = saveDialog.GetFilterIndex()

            if saveFmtInt == 0:  # Per ordering of wildcards above
                saveFmt = "emp"
            elif saveFmtInt == 1:
                saveFmt = "xml"
            else:
                saveFmt = "txt"

            filePath = saveDialog.GetPath()
            if '.' not in os.path.basename(filePath):
                filePath += ".{0}".format(saveFmt)

            self.waitDialog = wx.BusyInfo("Exporting skills needed...")
            sCharacter.backupSkills(filePath, saveFmt, self.getActiveFit(), self.closeWaitDialog)

        saveDialog.Destroy()

    def fileImportDialog(self, event):
        """Handles importing single/multiple EVE XML / EFT cfg fit files"""
        dlg = wx.FileDialog(
            self,
            "Open One Or More Fitting Files",
            wildcard=("EVE XML fitting files (*.xml)|*.xml|"
                      "EFT text fitting files (*.cfg)|*.cfg|"
                      "All Files (*)|*"),
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE
        )
        if dlg.ShowModal() == wx.ID_OK:
            self.progressDialog = wx.ProgressDialog(
                "Importing fits",
                " " * 100,  # set some arbitrary spacing to create width in window
                parent=self,
                style=wx.PD_CAN_ABORT | wx.PD_SMOOTH | wx.PD_ELAPSED_TIME | wx.PD_APP_MODAL
            )
            # self.progressDialog.message = None
            Port.importFitsThreaded(dlg.GetPaths(), self)
            self.progressDialog.ShowModal()
            try:
                dlg.Destroy()
            except RuntimeError:
                pyfalog.error("Tried to destroy an object that doesn't exist in <fileImportDialog>.")

    def backupToXml(self, event):
        """ Back up all fits to EVE XML file """
        defaultFile = "pyfa-fits-%s.xml" % strftime("%Y%m%d_%H%M%S", gmtime())

        saveDialog = wx.FileDialog(
            self,
            "Save Backup As...",
            wildcard="EVE XML fitting file (*.xml)|*.xml",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
            defaultFile=defaultFile,
        )

        if saveDialog.ShowModal() == wx.ID_OK:
            filePath = saveDialog.GetPath()
            if '.' not in os.path.basename(filePath):
                filePath += ".xml"

            sFit = Fit.getInstance()
            max_ = sFit.countAllFits()

            self.progressDialog = wx.ProgressDialog(
                "Backup fits",
                "Backing up %d fits to: %s" % (max_, filePath),
                maximum=max_,
                parent=self,
                style=wx.PD_CAN_ABORT | wx.PD_SMOOTH | wx.PD_ELAPSED_TIME | wx.PD_APP_MODAL
            )
            Port.backupFits(filePath, self)
            self.progressDialog.ShowModal()

    def exportHtml(self, event):
        from gui.utils.exportHtml import exportHtml
        sFit = Fit.getInstance()
        settings = HTMLExportSettings.getInstance()

        max_ = sFit.countAllFits()
        path = settings.getPath()

        if not os.path.isdir(os.path.dirname(path)):
            dlg = wx.MessageDialog(
                self,
                "Invalid Path\n\nThe following path is invalid or does not exist: \n%s\n\nPlease verify path location pyfa's preferences." % path,
                "Error",
                wx.OK | wx.ICON_ERROR
            )

            if dlg.ShowModal() == wx.ID_OK:
                return

        self.progressDialog = wx.ProgressDialog(
            "Backup fits",
            "Generating HTML file at: %s" % path,
            maximum=max_, parent=self,
            style=wx.PD_APP_MODAL | wx.PD_ELAPSED_TIME
        )

        exportHtml.getInstance().refreshFittingHtml(True, self.backupCallback)
        self.progressDialog.ShowModal()

    def backupCallback(self, info):
        if info == -1:
            self.closeProgressDialog()
        else:
            self.progressDialog.Update(info)

    def on_port_process_start(self):
        # flag for progress dialog.
        self.__progress_flag = True

    def on_port_processing(self, action, data=None):
        # 2017/03/29 NOTE: implementation like interface
        wx.CallAfter(
            self._on_port_processing, action, data
        )

        return self.__progress_flag

    def _on_port_processing(self, action, data):
        """
        While importing fits from file, the logic calls back to this function to
        update progress bar to show activity. XML files can contain multiple
        ships with multiple fits, whereas EFT cfg files contain many fits of
        a single ship. When iterating through the files, we update the message
        when we start a new file, and then Pulse the progress bar with every fit
        that is processed.

        action : a flag that lets us know how to deal with :data
                None: Pulse the progress bar
                1: Replace message with data
                other: Close dialog and handle based on :action (-1 open fits, -2 display error)
        """
        _message = None
        if action & IPortUser.ID_ERROR:
            self.closeProgressDialog()
            _message = "Import Error" if action & IPortUser.PROCESS_IMPORT else "Export Error"
            dlg = wx.MessageDialog(self,
                                   "The following error was generated\n\n%s\n\nBe aware that already processed fits were not saved" % data,
                                   _message, wx.OK | wx.ICON_ERROR)
            # if dlg.ShowModal() == wx.ID_OK:
            #     return
            dlg.ShowModal()
            return

        # data is str
        if action & IPortUser.PROCESS_IMPORT:
            if action & IPortUser.ID_PULSE:
                _message = ()
            # update message
            elif action & IPortUser.ID_UPDATE:  # and data != self.progressDialog.message:
                _message = data

            if _message is not None:
                self.__progress_flag, _unuse = self.progressDialog.Pulse(_message)
            else:
                self.closeProgressDialog()
                if action & IPortUser.ID_DONE:
                    self._openAfterImport(data)
        # data is tuple(int, str)
        elif action & IPortUser.PROCESS_EXPORT:
            if action & IPortUser.ID_DONE:
                self.closeProgressDialog()
            else:
                self.__progress_flag, _unuse = self.progressDialog.Update(data[0], data[1])

    def _openAfterImport(self, fits):
        if len(fits) > 0:
            if len(fits) == 1:
                fit = fits[0]
                wx.PostEvent(self, FitSelected(fitID=fit.ID, from_import=True))
                wx.PostEvent(self.shipBrowser, Stage3Selected(shipID=fit.shipID, back=True))
            else:
                fits.sort(key=lambda _fit: (_fit.ship.item.name, _fit.name))
                results = []
                for fit in fits:
                    results.append((
                        fit.ID,
                        fit.name,
                        fit.modifiedCoalesce,
                        fit.ship.item,
                        fit.notes
                    ))
                wx.PostEvent(self.shipBrowser, ImportSelected(fits=results, back=True))

    def closeProgressDialog(self):
        # Windows apparently handles ProgressDialogs differently. We can
        # simply Destroy it here, but for other platforms we must Close it
        if 'wxMSW' in wx.PlatformInfo:
            self.progressDialog.Destroy()
        else:
            self.progressDialog.EndModal(wx.ID_OK)
            self.progressDialog.Close()

    def importCharacter(self, event):
        """ Imports character XML file from EVE API """
        dlg = wx.FileDialog(
            self,
            "Open One Or More Character Files",
            wildcard="EVE API XML character files (*.xml)|*.xml|All Files (*)|*",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE
        )

        if dlg.ShowModal() == wx.ID_OK:
            self.supress_left_up = True
            self.waitDialog = wx.BusyInfo("Importing Character...")
            sCharacter = Character.getInstance()
            sCharacter.importCharacter(dlg.GetPaths(), self.importCharacterCallback)

    def importCharacterCallback(self):
        self.closeWaitDialog()
        wx.PostEvent(self, GE.CharListUpdated())

    def closeWaitDialog(self):
        del self.waitDialog

    def openGraphFrame(self, event):
        if not self.graphFrame:
            self.graphFrame = GraphFrame(self)

            if graphFrame.graphFrame_enabled:
                self.graphFrame.Show()
        elif graphFrame.graphFrame_enabled:
            self.graphFrame.SetFocus()

    def openWXInspectTool(self, event):
        if not InspectionTool().initialized:
            InspectionTool().Init()

        # Find a widget to be selected in the tree.  Use either the
        # one under the cursor, if any, or this frame.
        wnd, _ = wx.FindWindowAtPointer()
        if not wnd:
            wnd = self
        InspectionTool().Show(wnd, True)
