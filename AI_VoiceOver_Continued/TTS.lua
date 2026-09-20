setfenv(1, VoiceOver)

-- Text-to-speech fallback for voice lines that no data module provides.
-- Uses the client's built-in TTS (C_VoiceChat.SpeakText), which on Windows
-- speaks through the installed SAPI voices. Long texts are split into
-- sections and spoken one after another; the queue advances on the
-- VOICE_CHAT_TTS_PLAYBACK_FINISHED event, with a watchdog as a safety net.

TTS = {
    ---@type SoundData|nil
    active = nil,
    awaitingStart = false,
    utteranceID = nil,
}

local MAX_SECTION_BYTES = 800
local FEMALE_VOICE_PATTERNS = { "female", "zira", "hazel", "susan", "jenny", "aria", "hedda", "katja", "eva", "heera", "catherine", "linda", "michelle", "sonia", "libby", "hortense", "julie", "helena", "laura", "elsa", "irina", "maria" }
LOCALE_LANGUAGES = {
    enUS = "english", deDE = "german", frFR = "french", esES = "spanish", esMX = "spanish",
    itIT = "italian", ptBR = "portuguese", ruRU = "russian", koKR = "korean", zhCN = "chinese", zhTW = "chinese",
}

function TTS:IsAvailable()
    return Version.IsAnyRetail and C_VoiceChat and C_VoiceChat.SpeakText and C_VoiceChat.GetTtsVoices and true or false
end

function TTS:IsEnabled()
    return self:IsAvailable() and Addon.db.profile.TTS.Enabled
end

function TTS:GetVoices()
    if not self:IsAvailable() then
        return {}
    end
    return C_VoiceChat.GetTtsVoices() or {}
end

local function IsFemaleVoiceName(name)
    name = string.lower(name or "")
    for _, pattern in ipairs(FEMALE_VOICE_PATTERNS) do
        if string.find(name, pattern, 1, true) then
            return true
        end
    end
    return false
end

--- Returns the voice ID to use for the provided NPC sex (2 = male, 3 = female).
function TTS:GetVoiceID(sex)
    local voices = self:GetVoices()
    if not next(voices) then
        return nil
    end

    local wantFemale = sex == 3
    local configured = wantFemale and Addon.db.profile.TTS.FemaleVoice or Addon.db.profile.TTS.MaleVoice
    if configured then
        for _, voice in ipairs(voices) do
            if voice.voiceID == configured then
                return configured
            end
        end
    end

    -- Prefer voices that speak the client's language (Windows names them e.g. "... - German (Germany)")
    local language = LOCALE_LANGUAGES[GetLocale and GetLocale() or "enUS"]
    local candidates = {}
    if language then
        for _, voice in ipairs(voices) do
            if string.find(string.lower(voice.name or ""), language, 1, true) then
                table.insert(candidates, voice)
            end
        end
    end
    if not next(candidates) then
        candidates = voices
    end

    for _, voice in ipairs(candidates) do
        if IsFemaleVoiceName(voice.name) == wantFemale then
            return voice.voiceID
        end
    end
    return candidates[1].voiceID
end

function TTS:GetVoiceValues()
    local values = {}
    for _, voice in ipairs(self:GetVoices()) do
        values[voice.voiceID] = voice.name
    end
    return values
end

--- Removes UI escape sequences and characters that SAPI would interpret as XML markup.
local function CleanText(text)
    text = text or ""
    text = string.gsub(text, "|c%x%x%x%x%x%x%x%x", "")
    text = string.gsub(text, "|r", "")
    text = string.gsub(text, "|n", " ")
    text = string.gsub(text, "|T.-|t", "")
    text = string.gsub(text, "|H.-|h(.-)|h", "%1")
    text = string.gsub(text, "&", " and ")
    text = string.gsub(text, "[<>]", " ")
    text = string.gsub(text, "%s+", " ")
    return strtrim(text)
end

--- Splits text into sections of at most MAX_SECTION_BYTES, preferring sentence and word boundaries.
local function SplitText(text)
    local sections = {}
    while string.len(text) > MAX_SECTION_BYTES do
        local cut
        local window = string.sub(text, 1, MAX_SECTION_BYTES)
        -- Last sentence end inside the window
        for position in string.gmatch(window, "()[%.!%?]%s") do
            cut = position
        end
        if not cut or cut < MAX_SECTION_BYTES / 3 then
            cut = nil
            for position in string.gmatch(window, "()%s") do
                cut = position - 1
            end
        end
        if not cut or cut < 1 then
            cut = MAX_SECTION_BYTES
            -- Don't split a multi-byte UTF-8 character
            while cut > 1 and string.byte(text, cut + 1) and string.byte(text, cut + 1) >= 128 and string.byte(text, cut + 1) < 192 do
                cut = cut - 1
            end
        end
        table.insert(sections, strtrim(string.sub(text, 1, cut)))
        text = strtrim(string.sub(text, cut + 1))
    end
    if text ~= "" then
        table.insert(sections, text)
    end
    return sections
end

---@param soundData SoundData
---@return boolean canSpeak
function TTS:PrepareSound(soundData)
    if not self:IsEnabled() then
        return false
    end
    if Enums.SoundEvent:IsGossipEvent(soundData.event) and not Addon.db.profile.TTS.Gossip then
        return false
    end

    local text = CleanText(soundData.text)
    if text == "" then
        return false
    end
    if Enums.SoundEvent:IsQuestEvent(soundData.event) and Addon.db.profile.TTS.ReadTitle and soundData.title and soundData.event == Enums.SoundEvent.QuestAccept then
        text = CleanText(soundData.title) .. ". " .. text
    end

    soundData.isTTS = true
    soundData.ttsSections = SplitText(text)
    soundData.ttsSection = 1
    soundData.fileName = "tts:" .. soundData.event .. ":" .. (soundData.questID or 0) .. ":" .. text
    soundData.length = string.len(text) / 14
    return true
end

local function EstimateSectionDuration(section)
    local rate = Addon.db.profile.TTS.Rate or 0
    local speed = 1 + rate * 0.1
    if speed < 0.3 then speed = 0.3 end
    return string.len(section) / (14 * speed) + 8
end

function TTS:SpeakCurrentSection(soundData)
    local section = soundData.ttsSections[soundData.ttsSection]
    if not section then
        return false
    end

    local voiceID = self:GetVoiceID(soundData.npcSex)
    if not voiceID then
        Debug:Record("tts-no-voice", "No text-to-speech voices are installed")
        print("|cFFFF4040VoiceOver: no text-to-speech voices found. Check the Windows speech voices and WoW's Text to Speech settings.|r")
        return false
    end

    if soundData.ttsWatchdog then
        Addon:CancelTimer(soundData.ttsWatchdog)
    end
    soundData.ttsWatchdog = Addon:ScheduleTimer(function()
        Debug:Record("tts-watchdog", "No playback-finished event received, advancing")
        self:OnSectionFinished(soundData)
    end, EstimateSectionDuration(section))
    soundData.nextSoundTimer = soundData.ttsWatchdog

    self.active = soundData
    self.awaitingStart = true
    self.utteranceID = nil
    C_VoiceChat.SpeakText(voiceID, section, Addon.db.profile.TTS.Rate or 0, Addon.db.profile.TTS.Volume or 100, false)
    return true
end

---@param soundData SoundData
function TTS:Play(soundData)
    soundData.handle = true -- Marks the sound as stoppable for SoundQueue:CanBePaused
    if not self:SpeakCurrentSection(soundData) then
        -- Nothing could be spoken, let the queue move on
        Addon:ScheduleTimer(function()
            SoundQueue:RemoveSoundFromQueue(soundData, true)
        end, 0)
    end
end

---@param soundData SoundData
function TTS:Stop(soundData)
    if soundData.ttsWatchdog then
        Addon:CancelTimer(soundData.ttsWatchdog)
        soundData.ttsWatchdog = nil
    end
    soundData.handle = nil
    if self.active == soundData then
        self.active = nil
        self.awaitingStart = false
        self.utteranceID = nil
        -- StopSpeakingText is global, so only call it while we own the speech
        C_VoiceChat.StopSpeakingText()
    end
end

function TTS:OnSectionFinished(soundData)
    if self.active ~= soundData then
        return
    end
    soundData.ttsSection = soundData.ttsSection + 1
    if soundData.ttsSections[soundData.ttsSection] then
        self:SpeakCurrentSection(soundData)
    else
        self.active = nil
        self.utteranceID = nil
        if soundData.ttsWatchdog then
            Addon:CancelTimer(soundData.ttsWatchdog)
            soundData.ttsWatchdog = nil
        end
        SoundQueue:RemoveSoundFromQueue(soundData, true)
    end
end

local eventFrame = CreateFrame("Frame")
eventFrame:SetScript("OnEvent", function(_, event, utteranceID, status)
    local soundData = TTS.active
    if not soundData then
        return
    end

    if event == "VOICE_CHAT_TTS_PLAYBACK_STARTED" then
        if TTS.awaitingStart then
            TTS.awaitingStart = false
            TTS.utteranceID = utteranceID
        end
    elseif event == "VOICE_CHAT_TTS_PLAYBACK_FINISHED" then
        if TTS.utteranceID == nil or TTS.utteranceID == utteranceID then
            TTS:OnSectionFinished(soundData)
        end
    elseif event == "VOICE_CHAT_TTS_PLAYBACK_FAILED" then
        if TTS.utteranceID == nil or TTS.utteranceID == utteranceID then
            Debug:Record("tts-failed", format("Text-to-speech playback failed (status %s)", tostring(status)))
            TTS:OnSectionFinished(soundData)
        end
    end
end)
if TTS:IsAvailable() then
    eventFrame:RegisterEvent("VOICE_CHAT_TTS_PLAYBACK_STARTED")
    eventFrame:RegisterEvent("VOICE_CHAT_TTS_PLAYBACK_FINISHED")
    eventFrame:RegisterEvent("VOICE_CHAT_TTS_PLAYBACK_FAILED")
end

function TTS:SpeakTest()
    if not self:IsAvailable() then
        print("|cFFFF4040VoiceOver: this client has no text-to-speech API.|r")
        return
    end
    SoundQueue:RemoveAllSoundsFromQueue()
    local soundData = {
        event = Enums.SoundEvent.QuestAccept,
        questID = 0,
        name = "VoiceOver",
        title = "Text to speech test",
        text = "Greetings, adventurer. This is how quests without a recorded voiceover will sound.",
        npcSex = 2,
    }
    local enabled = Addon.db.profile.TTS.Enabled
    Addon.db.profile.TTS.Enabled = true
    local prepared = self:PrepareSound(soundData)
    Addon.db.profile.TTS.Enabled = enabled
    if prepared then
        SoundQueue:AddSoundToQueue(soundData, true)
    end
end
