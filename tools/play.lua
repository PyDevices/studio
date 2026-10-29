-- SPDX-FileCopyrightText: 2026 PyDevices
--
-- SPDX-License-Identifier: MIT
--
-- REAPER plays the gate's song to the sound module and quits.
--
-- tools/gate.py writes play-config.lua beside this script with SONG (the
-- folder tools/song.py wrote), PORT (the MIDI output's exact name),
-- PROJECT (where to save the project) and LOG. Each part's file goes on a
-- track of its own, sent to PORT on its own channels; then REAPER plays
-- the project from the start, in real time, to its end plus a second, and
-- quits.

local here = ({reaper.get_action_context()})[2]:match("^(.*[\\/])")
dofile(here .. "play-config.lua")

local log = io.open(LOG, "w")
local function say(s) log:write(s .. "\n"); log:flush() end

local port = nil
for i = 0, reaper.GetNumMIDIOutputs() - 1 do
  local ok, name = reaper.GetMIDIOutputName(i, "")
  if ok and name == PORT then port = i end
end
if not port then
  say("no MIDI output named " .. PORT)
  reaper.Main_OnCommand(40004, 0)
  return
end
say("port " .. port .. " " .. PORT)

local files = {}
local i = 0
while true do
  local f = reaper.EnumerateFiles(SONG, i)
  if not f then break end
  if f:match("%.mid$") then files[#files + 1] = f end
  i = i + 1
end
table.sort(files)

for n, f in ipairs(files) do
  reaper.InsertTrackAtIndex(n - 1, false)
  local track = reaper.GetTrack(0, n - 1)
  reaper.GetSetMediaTrackInfo_String(track, "P_NAME", f:gsub("%.mid$", ""), true)
  reaper.SetOnlyTrackSelected(track)
  reaper.SetEditCurPos(0, false, false)
  reaper.InsertMedia(SONG .. "\\" .. f, 0)
  reaper.SetMediaTrackInfo_Value(track, "I_MIDIHWOUT", port << 5) -- channel 0: the file's own
  reaper.SetMediaTrackInfo_Value(track, "B_MAINSEND", 0)
  say("track " .. n .. " " .. f .. " items " .. reaper.CountTrackMediaItems(track))
end

local length = reaper.GetProjectLength(0)
reaper.Main_SaveProjectEx(0, PROJECT, 0)
say("length " .. length .. " saved " .. PROJECT)
reaper.SetEditCurPos(0, false, false)
local started = nil
local asked = reaper.time_precise()

local function wait()
  if not started then
    -- Play from a deferred call, once the audio device is running.
    if reaper.Audio_IsRunning() == 0 and reaper.time_precise() - asked < 10 then
      reaper.defer(wait)
      return
    end
    reaper.Main_OnCommand(1007, 0) -- Transport: Play
    started = reaper.time_precise()
    say(string.format("play after %.2f s, audio %d, state %d", started - asked, reaper.Audio_IsRunning(), reaper.GetPlayState()))
  end
  local pos = reaper.GetPlayPosition()
  local t = reaper.time_precise() - started
  if t >= (logged or 0) then
    say(string.format("at %.1f s: state %d, position %.2f s, audio %d", t, reaper.GetPlayState(), pos, reaper.Audio_IsRunning()))
    logged = (logged or 0) + 5
  end
  if t < length + 1 and (t < 2 or reaper.GetPlayState() & 1 == 1) then
    reaper.defer(wait)
    return
  end
  reaper.OnStopButton()
  say(string.format("played %.2f s, reached %.2f s of %.2f", t, pos, length))
  log:close()
  reaper.Main_OnCommand(40026, 0) -- File: Save project (the session file the gate opened)
  reaper.Main_OnCommand(40004, 0) -- File: Quit
end
reaper.defer(wait)
