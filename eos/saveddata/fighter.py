#===============================================================================
# Copyright (C) 2010 Diego Duclos
#
# This file is part of eos.
#
# eos is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
#
# eos is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with eos.  If not, see <http://www.gnu.org/licenses/>.
#===============================================================================

from eos.modifiedAttributeDict import ModifiedAttributeDict, ItemAttrShortcut, ChargeAttrShortcut
from eos.effectHandlerHelpers import HandledItem, HandledCharge
from sqlalchemy.orm import validates, reconstructor
import eos.db
import logging

logger = logging.getLogger(__name__)

class Fighter(HandledItem, HandledCharge, ItemAttrShortcut, ChargeAttrShortcut):
    DAMAGE_TYPES = ("em", "kinetic", "explosive", "thermal")
    MINING_ATTRIBUTES = ("miningAmount",)

    def __init__(self, item):
        """Initialize a fighter from the program"""
        self.__item = item
        print self.__item.category.name
        if self.isInvalid:
            raise ValueError("Passed item is not a Fighter")

        self.itemID = item.ID if item is not None else None
        self.amount = 0
        self.amountActive = 0
        self.projected = False
        self.build()

    @reconstructor
    def init(self):
        """Initialize a fighter from the database and validate"""
        self.__item = None

        if self.itemID:
            self.__item = eos.db.getItem(self.itemID)
            if self.__item is None:
                logger.error("Item (id: %d) does not exist", self.itemID)
                return

        if self.isInvalid:
            logger.error("Item (id: %d) is not a Fighter", self.itemID)
            return

        self.build()

    def build(self):
        """ Build object. Assumes proper and valid item already set """
        self.__charge = None
        self.__dps = None
        self.__volley = None
        self.__miningyield = None
        self.__itemModifiedAttributes = ModifiedAttributeDict()
        self.__itemModifiedAttributes.original = self.__item.attributes
        self.__itemModifiedAttributes.overrides = self.__item.overrides

        self.__chargeModifiedAttributes = ModifiedAttributeDict()
        chargeID = self.getModifiedItemAttr("entityMissileTypeID")
        if chargeID is not None:
            charge = eos.db.getItem(int(chargeID))
            self.__charge = charge
            self.__chargeModifiedAttributes.original = charge.attributes
            self.__chargeModifiedAttributes.overrides = charge.overrides

    @property
    def itemModifiedAttributes(self):
        return self.__itemModifiedAttributes

    @property
    def chargeModifiedAttributes(self):
        return self.__chargeModifiedAttributes

    @property
    def isInvalid(self):
        return self.__item is None or self.__item.category.name != "Fighter"

    @property
    def item(self):
        return self.__item

    @property
    def charge(self):
        return self.__charge

    @property
    def dealsDamage(self):
        for attr in ("emDamage", "kineticDamage", "explosiveDamage", "thermalDamage"):
            if attr in self.itemModifiedAttributes or attr in self.chargeModifiedAttributes:
                return True

    @property
    def mines(self):
        if "miningAmount" in self.itemModifiedAttributes:
            return True

    @property
    def hasAmmo(self):
        return self.charge is not None

    @property
    def dps(self):
        return self.damageStats()

    def damageStats(self, targetResists = None):
        if self.__dps == None:
            self.__volley = 0
            self.__dps = 0
            if self.dealsDamage is True and self.amountActive > 0:
                if self.hasAmmo:
                    attr = "missileLaunchDuration"
                    getter = self.getModifiedChargeAttr
                else:
                    attr =  "speed"
                    getter = self.getModifiedItemAttr

                cycleTime = self.getModifiedItemAttr(attr)

                volley = sum(map(lambda d: (getter("%sDamage"%d) or 0) * (1-getattr(targetResists, "%sAmount"%d, 0)), self.DAMAGE_TYPES))
                volley *= self.amountActive
                volley *= self.getModifiedItemAttr("damageMultiplier") or 1
                self.__volley = volley
                self.__dps = volley / (cycleTime / 1000.0)

        return self.__dps, self.__volley

    @property
    def miningStats(self):
        if self.__miningyield == None:
            if self.mines is True and self.amountActive > 0:
                attr = "duration"
                getter = self.getModifiedItemAttr

                cycleTime = self.getModifiedItemAttr(attr)
                volley = sum(map(lambda d: getter(d), self.MINING_ATTRIBUTES)) * self.amountActive
                self.__miningyield = volley / (cycleTime / 1000.0)
            else:
                self.__miningyield = 0

        return self.__miningyield

    @property
    def maxRange(self):
        attrs = ("shieldTransferRange", "powerTransferRange",
                 "energyDestabilizationRange", "empFieldRange",
                 "ecmBurstRange", "maxRange")
        for attr in attrs:
            maxRange = self.getModifiedItemAttr(attr)
            if maxRange is not None: return maxRange
        if self.charge is not None:
            delay = self.getModifiedChargeAttr("explosionDelay")
            speed = self.getModifiedChargeAttr("maxVelocity")
            if delay is not None and speed is not None:
                return delay / 1000.0 * speed

    # Had to add this to match the falloff property in modules.py
    # Fscking ship scanners. If you find any other falloff attributes,
    # Put them in the attrs tuple.
    @property
    def falloff(self):
        attrs = ("falloff", "falloffEffectiveness")
        for attr in attrs:
            falloff = self.getModifiedItemAttr(attr)
            if falloff is not None: return falloff

    @validates("ID", "itemID", "chargeID", "amount", "amountActive")
    def validator(self, key, val):
        map = {"ID": lambda val: isinstance(val, int),
               "itemID" : lambda val: isinstance(val, int),
               "chargeID" : lambda val: isinstance(val, int),
               "amount" : lambda val: isinstance(val, int) and val >= 0,
               "amountActive" : lambda val: isinstance(val, int) and val <= self.amount and val >= 0}

        if map[key](val) == False: raise ValueError(str(val) + " is not a valid value for " + key)
        else: return val

    def clear(self):
        self.__dps = None
        self.__volley = None
        self.__miningyield = None
        self.itemModifiedAttributes.clear()
        self.chargeModifiedAttributes.clear()

    def canBeApplied(self, projectedOnto):
        """Check if fighter can engage specific fitting"""
        item = self.item
        # Do not allow to apply offensive modules on ship with offensive module immunite, with few exceptions
        # (all effects which apply instant modification are exception, generally speaking)
        if item.offensive and projectedOnto.ship.getModifiedItemAttr("disallowOffensiveModifiers") == 1:
            offensiveNonModifiers = set(("energyDestabilizationNew", "leech", "energyNosferatuFalloff", "energyNeutralizerFalloff"))
            if not offensiveNonModifiers.intersection(set(item.effects)):
                return False
        # If assistive modules are not allowed, do not let to apply these altogether
        if item.assistive and projectedOnto.ship.getModifiedItemAttr("disallowAssistance") == 1:
            return False
        else:
            return True

    def calculateModifiedAttributes(self, fit, runTime, forceProjected = False):
        if self.projected or forceProjected:
            context = "projected", "fighter"
            projected = True
        else:
            context = ("fighter",)
            projected = False

        for effect in self.item.effects.itervalues():
            if effect.runTime == runTime and \
            ((projected == True and effect.isType("projected")) or \
             projected == False and effect.isType("passive")):
                i = 0
                while i != self.amountActive:
                    effect.handler(fit, self, context)
                    i += 1

        if self.charge:
            for effect in self.charge.effects.itervalues():
                if effect.runTime == runTime:
                    effect.handler(fit, self, ("fighterCharge",))

    def __deepcopy__(self, memo):
        copy = Fighter(self.item)
        copy.amount = self.amount
        copy.amountActive = self.amountActive
        return copy

    def fits(self, fit):
        return True

        fitDroneGroupLimits = set()
        for i in xrange(1, 3):
            groneGrp = fit.ship.getModifiedItemAttr("allowedDroneGroup%d" % i)
            if groneGrp is not None:
                fitDroneGroupLimits.add(int(groneGrp))
        if len(fitDroneGroupLimits) == 0:
            return True
        if self.item.groupID in fitDroneGroupLimits:
            return True
        return False