"""The planned return from an absence, editable straight from a dashboard.

Away mode holds every room at the away temperature and pre-heats them so the
comfort temperature is reached when you get back. That only works if the
integration knows when "back" is, so the time is an entity of its own rather
than a service argument.
"""

from __future__ import annotations

from datetime import date, datetime, time

from homeassistant.components.datetime import DateTimeEntity
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import ThrifthermConfigEntry, ThrifthermCoordinator
from .entity import SettingEntity

PROVISIONAL_RETURN = time(23, 59)  # whole minutes: the time field keeps the seconds of the old value
MIDNIGHT = time(0)  # what the date field sends while nothing is planned


async def async_setup_entry(hass: HomeAssistant, entry: ThrifthermConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([AwayReturnDateTime(entry.runtime_data)])


def _time_of_day(value: datetime) -> time:
    """Hour, minute and second in Home Assistant's time zone; the state shows no more."""
    return dt_util.as_local(value).time().replace(microsecond=0, fold=0)


def _at(day: date, time_of_day: time) -> datetime:
    """That time of day in Home Assistant's time zone.

    A time the clock change skips or repeats is read with the offset from before
    the change, as the browser reads it (fold 0).
    """
    return datetime.combine(day, time_of_day, tzinfo=dt_util.get_default_time_zone())


def _is_at(value: datetime, time_of_day: time) -> bool:
    """Whether the value is that time of day on its own day, compared as points in time to the second.

    On the day summer time starts, the date field turns a planned 02:30 into 03:30;
    comparing the time of day on the clock would miss that.
    """
    return dt_util.as_utc(value).replace(microsecond=0) == dt_util.as_utc(_at(dt_util.as_local(value).date(), time_of_day))


def return_meant(value: datetime, planned: datetime | None, now: datetime, provisional: bool = False) -> tuple[datetime, bool]:
    """The return a date or time field means, and whether it is only provisional.

    Home Assistant's date and time fields (more-info dialog, entities card) send
    each field on its own, computed from the state they show, which `planned` and
    `provisional` are: a new date keeps the time of day of that state, or is
    midnight while nothing is planned, and the time field sends nothing while
    nothing is planned. A date without a time is a placeholder:

    - Nothing planned: midnight of any day, today or later, becomes a provisional
      return at 23:59 that day. Away mode ends then, nothing pre-heats for it, and
      the time picked next is the real return. 23:59 of a past day, or of today
      once it has passed, is refused like any past time.
    - A real plan: the date step keeps the planned time and stays real. Moved to
      today when that time has passed, it becomes a provisional 23:59 today.
    - A provisional plan: the date step keeps 23:59 and stays provisional; any
      other time is real.

    A return already reached stays the state until the next cycle ends away mode.
    A future value is still read against it, as the date field keeps its time of
    day: a provisional 23:59 moved to another day stays provisional, a real time
    stays real. A past value is read as if nothing were planned, as after that
    cycle: setting the reached time again is refused, and midnight of today
    becomes the placeholder, refused too once 23:59 has passed. Any other past
    value is passed on and refused, so an automation that sets a past time gets
    an error, not a silent 23:59.
    """
    now = dt_util.as_utc(now)  # points in time: within one zone Python compares the clock and ignores the repeated hour
    if planned is not None and planned <= now and value <= now:
        planned, provisional = None, False  # reached: a past value means what it would after the next cycle
    if planned is None:
        if _is_at(value, MIDNIGHT):
            return _at(dt_util.as_local(value).date(), PROVISIONAL_RETURN), True
        return value, False
    if value > now:
        return value, provisional and _is_at(value, PROVISIONAL_RETURN)
    today = dt_util.as_local(now).date()
    if dt_util.as_local(value).date() == today and _is_at(value, _time_of_day(planned)):
        return _at(today, PROVISIONAL_RETURN), True
    return value, False  # the coordinator refuses it with the translated message


class AwayReturnDateTime(SettingEntity, DateTimeEntity):
    """When you expect to be back. Empty while no return is planned."""

    _attr_translation_key = "away_return"
    _attr_icon = "mdi:home-clock"

    def __init__(self, coordinator: ThrifthermCoordinator) -> None:
        super().__init__(coordinator, "away_return")
        self.suggest_english_entity_id()

    @property
    def native_value(self) -> datetime | None:
        ts = self.coordinator.away_return_ts
        return None if ts is None else dt_util.utc_from_timestamp(ts)

    @property
    def extra_state_attributes(self) -> dict:
        return {"provisional": self.coordinator.away_return_provisional}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # written the moment the return changes, not only when the cycle ends: the fields compute from this state
        self.async_on_remove(self.coordinator.async_add_away_listener(self.async_write_ha_state))

    def _shown(self) -> tuple[datetime | None, bool]:
        """The return and its mark as the state shows them; nothing planned while it is unknown.

        The fields compute what they send from the state they were last sent, so a value is read
        against that state rather than against the coordinator, which can be ahead of it. A step
        the browser computed before the latest state reached it is read against that latest state
        all the same. Two such cases would show in the value itself, but a rule for them would break
        the rules for set_value from automations, so it is deliberately not made. While the entity is
        unavailable, Home Assistant drops set_value before it gets here, so "unavailable" is read like
        "unknown" only to be safe.
        """
        shown = self.hass.states.get(self.entity_id)
        if shown is None or shown.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None, False
        planned = dt_util.parse_datetime(shown.state)
        return planned, planned is not None and shown.attributes.get("provisional") is True

    async def async_set_value(self, value: datetime) -> None:
        # Planning a return means being away until then, so this switches the mode too.
        # "Back home" (clear_away) ends both the absence and the plan.
        # Only this entity reads a date without a time as a placeholder; the set_away action stays strict for automations.
        planned, provisional = self._shown()
        meant, provisional = return_meant(value, planned, dt_util.now(), provisional)
        await self.coordinator.async_set_away(meant.timestamp(), provisional=provisional)
