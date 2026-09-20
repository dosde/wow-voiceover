setfenv(1, VoiceOver)

-- Collects quest and gossip texts that no data module has a recording for.
-- They are saved in VoiceOverDB.global.MissingLines (SavedVariables) so that
-- Tools\generate_voices.py can turn them into a generated data module.
--
-- To pick a fitting voice, each entry also stores what we know about the
-- speaker: sex, the 3D model's FileDataID (which identifies race and sex
-- exactly for humanoid NPCs), the derived race and the zone as a fallback.

MissingLines = {}

local EVENT_NAMES = {
    [Enums.SoundEvent.QuestAccept] = "accept",
    [Enums.SoundEvent.QuestProgress] = "progress",
    [Enums.SoundEvent.QuestComplete] = "complete",
    [Enums.SoundEvent.QuestGreeting] = "gossip",
    [Enums.SoundEvent.Gossip] = "gossip",
}

-- Character model FileDataIDs (classic and HD versions) mapped to race names
local MODEL_RACES = {
    [116921] = "bloodelf", [1100258] = "bloodelf", [117170] = "bloodelf", [1100087] = "bloodelf",
    [117400] = "draenei", [117412] = "draenei", -- broken
    [117437] = "draenei", [1022598] = "draenei", [117721] = "draenei", [1005887] = "draenei",
    [118135] = "dwarf", [950080] = "dwarf", [118355] = "dwarf", [878772] = "dwarf",
    [118652] = "orc", [118653] = "orc", [118654] = "orc", [118667] = "orc", -- fel orc
    [118798] = "troll", [232863] = "troll", -- forest/ice troll
    [119063] = "gnome", [940356] = "gnome", [119159] = "gnome", [900914] = "gnome",
    [119369] = "goblin", [119376] = "goblin",
    [119563] = "human", [1000764] = "human", [119940] = "human", [1011653] = "human",
    [120263] = "naga", [120294] = "naga",
    [120590] = "nightelf", [921844] = "nightelf", [120791] = "nightelf", [974343] = "nightelf",
    [121087] = "orc", [949470] = "orc", [121287] = "orc", [917116] = "orc",
    [121608] = "undead", [997378] = "undead", [121768] = "undead", [959310] = "undead",
    [121942] = "undead", [233367] = "undead", -- skeletons
    [233878] = "tauren", [121961] = "tauren", [986648] = "tauren", [122055] = "tauren", [968705] = "tauren",
    [122414] = "troll", [1018060] = "troll", [122560] = "troll", [1022938] = "troll",
    [122738] = "tuskarr", [122815] = "vrykul",
}

--- Replaces the player's own name, class and race with the game's placeholders, so
--- collected lines can be shared with others without carrying personal data.
local function Anonymize(text)
    if not text or text == "" then
        return text
    end
    local replacements = {
        { UnitName and UnitName("player"), "$N" },
        { UnitClass and UnitClass("player"), "$C" },
        { UnitRace and UnitRace("player"), "$R" },
    }
    for _, pair in ipairs(replacements) do
        local value, placeholder = pair[1], pair[2]
        if value and value ~= "" then
            local pattern = string.gsub(value, "(%W)", "%%%1")
            text = string.gsub(text, pattern, placeholder)
        end
    end
    return text
end

local function GetStore()
    local global = Addon.db.global
    global.MissingLines = global.MissingLines or {}
    return global.MissingLines
end

-- Hidden model used to read the speaker's model FileDataID
local probe
local function GetProbe()
    if not probe then
        probe = CreateFrame("PlayerModel", nil, UIParent)
        probe:SetSize(1, 1)
        probe:SetPoint("TOPLEFT", UIParent, "TOPLEFT", -100, 100)
        probe:SetAlpha(0)
    end
    return probe
end

--- Fills entry.model and entry.race once the model has loaded (models load asynchronously).
local function CaptureModel(entry, unit, creatureID)
    if not probe and not CreateFrame then
        return
    end
    local model = GetProbe()
    model:Show()
    model:ClearModel()
    if unit and UnitExists(unit) then
        model:SetUnit(unit)
    elseif creatureID then
        model:SetCreature(creatureID)
    else
        return
    end

    local attempts = 0
    local function check()
        attempts = attempts + 1
        local fileID = model:GetModelFileID()
        if fileID then
            entry.model = fileID
            entry.race = MODEL_RACES[fileID] or entry.race
            model:Hide()
        elseif attempts < 15 then
            Addon:ScheduleTimer(check, 0.2)
        else
            model:Hide()
        end
    end
    check()
end

---@param soundData SoundData
function MissingLines:Record(soundData)
    if not Addon.db.profile.TTS.CollectMissing then
        return
    end
    local text = Anonymize(soundData.text)
    if not text or text == "" then
        return
    end
    local eventName = EVENT_NAMES[soundData.event]
    if not eventName then
        return
    end

    local npcID, npcType
    if soundData.unitGUID then
        local type = Utils:GetGUIDType(soundData.unitGUID)
        if type and Enums.GUID:CanHaveID(type) then
            npcID = Utils:GetIDFromGUID(soundData.unitGUID)
            npcType = Enums.GUID:IsCreature(type) and "creature" or type == Enums.GUID.GameObject and "object" or type == Enums.GUID.Item and "item" or nil
        end
    end

    local key
    if eventName == "gossip" then
        key = format("gossip:%s:%s", tostring(npcID or soundData.name or "?"), text)
    elseif soundData.questID and soundData.questID ~= 0 then
        key = format("%d-%s", soundData.questID, eventName)
    else
        key = format("noid:%s:%s", eventName, soundData.title or text)
    end
    local locale = GetLocale and GetLocale() or "enUS"
    if locale ~= "enUS" then
        key = locale .. ":" .. key -- Keep lines of different client languages apart
    end

    local store = GetStore()
    local existing = store[key]
    -- Keep an existing entry unless the new one knows the speaker and the old one didn't (e.g. first seen in the quest log)
    if existing and (existing.npcID or not npcID) then
        return
    end

    local entry = {
        event = eventName,
        questID = soundData.questID ~= 0 and soundData.questID or nil,
        title = Anonymize(soundData.title),
        npc = soundData.name,
        npcID = npcID,
        npcType = npcType,
        sex = soundData.npcSex,
        text = text,
        zone = GetRealZoneText and GetRealZoneText() or nil,
        subzone = GetSubZoneText and GetSubZoneText() or nil,
        locale = GetLocale and GetLocale() or nil,
        time = time(),
    }
    store[key] = entry

    if npcType == "creature" and Version.IsAnyRetail then
        local unit = UnitExists("questnpc") and "questnpc" or UnitExists("npc") and "npc" or nil
        pcall(CaptureModel, entry, unit, npcID)
    end
    Debug:Record("missing-line", format("Recorded missing voice line %s", key))
end

--- Records the description of every quest in the player's log that has no recording.
--- The client hands those texts out without talking to the quest giver, so this fills
--- gaps from quests picked up before the addon was installed.
---@return number scanned, number recorded
function MissingLines:ScanQuestLog()
    local entries, recorded = 0, 0
    local previous = C_QuestLog and C_QuestLog.GetSelectedQuest and C_QuestLog.GetSelectedQuest()

    local function RecordQuest(questID, title)
        if not questID or questID == 0 then
            return
        end
        entries = entries + 1
        if DataModules:PrepareSound({ event = Enums.SoundEvent.QuestAccept, questID = questID }) then
            return -- a recording already exists
        end
        local before = MissingLines:Count()
        if C_QuestLog and C_QuestLog.SetSelectedQuest then
            C_QuestLog.SetSelectedQuest(questID)
        elseif SelectQuestLogEntry then
            SelectQuestLogEntry(questID)
        end
        local description = GetQuestLogQuestText and GetQuestLogQuestText()
        if description and description ~= "" then
            local type, id = DataModules:GetQuestLogQuestGiverTypeAndID(questID)
            self:Record({
                event = Enums.SoundEvent.QuestAccept,
                questID = questID,
                title = title,
                text = description,
                name = id and DataModules:GetObjectName(type, id) or "Quest Log",
                unitGUID = id and Enums.GUID:CanHaveID(type) and Utils:MakeGUID(type, id) or nil,
                npcSex = false,
            })
            if MissingLines:Count() > before then
                recorded = recorded + 1
            end
        end
    end

    if C_QuestLog and C_QuestLog.GetNumQuestLogEntries and C_QuestLog.GetInfo then
        for index = 1, (C_QuestLog.GetNumQuestLogEntries()) do
            local info = C_QuestLog.GetInfo(index)
            if info and not info.isHeader then
                RecordQuest(info.questID, info.title)
            end
        end
    elseif GetNumQuestLogEntries and GetQuestLogTitle then
        for index = 1, (GetNumQuestLogEntries()) do
            local title, _, _, isHeader, _, _, _, questID = GetQuestLogTitle(index)
            if not isHeader then
                RecordQuest(questID, title)
            end
        end
    end

    if previous and previous ~= 0 and C_QuestLog and C_QuestLog.SetSelectedQuest then
        C_QuestLog.SetSelectedQuest(previous)
    end
    Debug:Record("questlog-scan", format("Scanned %d quests, recorded %d", entries, recorded))
    return entries, recorded
end

function MissingLines:Count()
    local count = 0
    for _ in pairs(GetStore()) do
        count = count + 1
    end
    return count
end

--- Counts how many collected lines have a recording by now, and drops those entries.
---@return number voiced, number waiting
function MissingLines:Prune()
    local store, voiced, waiting = GetStore(), 0, 0
    for key, entry in pairs(store) do
        local done = false
        if entry.event ~= "gossip" and entry.questID then
            done = DataModules:PrepareSound({ event = Enums.SoundEvent.QuestAccept, questID = entry.questID,
                text = entry.text, name = entry.npc }) and entry.event == "accept"
        end
        if done then
            store[key] = nil
            voiced = voiced + 1
        else
            waiting = waiting + 1
        end
    end
    return voiced, waiting
end

--- Numbers for the status panel: recordings available and lines still missing.
---@return number recordings, number waiting, number modules
function MissingLines:GetStatus()
    local recordings, modules = 0, 0
    for _, module in DataModules:GetModules() do
        modules = modules + 1
        for _ in pairs(module.SoundLengthLookupByFileName or {}) do
            recordings = recordings + 1
        end
    end
    return recordings, self:Count(), modules
end

function MissingLines:Clear()
    Addon.db.global.MissingLines = {}
end
