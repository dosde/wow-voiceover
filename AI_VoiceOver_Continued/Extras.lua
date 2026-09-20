setfenv(1, VoiceOver)

-- Comfort features for modern clients:
--   * Subtitles: shows the spoken text above the VoiceOver frame
--   * Music ducking: lowers the music volume while a voiceover is playing
--   * Combat pause: pauses the queue when entering combat and resumes after

Extras = {
    pausedForCombat = false,
}

------------------------------------------------------------
-- Subtitles

function Extras:GetSubtitleFrame()
    if self.subtitle or not SoundQueueUI.frame then
        return self.subtitle
    end
    local frame = CreateFrame("Frame", "VoiceOverSubtitleFrame", SoundQueueUI.frame, "BackdropTemplate")
    frame:SetPoint("BOTTOMLEFT", SoundQueueUI.frame, "TOPLEFT", 0, 6)
    frame:SetPoint("BOTTOMRIGHT", SoundQueueUI.frame, "TOPRIGHT", 0, 6)
    frame:SetBackdrop({
        bgFile = [[Interface\Tooltips\UI-Tooltip-Background]],
        edgeFile = [[Interface\Tooltips\UI-Tooltip-Border]],
        tile = true, tileSize = 16, edgeSize = 12,
        insets = { left = 3, right = 3, top = 3, bottom = 3 },
    })
    frame:SetBackdropColor(0, 0, 0, 0.7)
    frame:SetBackdropBorderColor(0.4, 0.4, 0.4, 0.8)
    frame.text = frame:CreateFontString(nil, "ARTWORK", "GameFontHighlight")
    frame.text:SetPoint("TOPLEFT", 10, -8)
    frame.text:SetPoint("TOPRIGHT", -10, -8)
    frame.text:SetJustifyH("LEFT")
    frame.text:SetJustifyV("TOP")
    frame.text:SetSpacing(2)
    frame:Hide()
    self.subtitle = frame
    return frame
end

local function CleanSubtitle(text)
    text = string.gsub(text or "", "|n", "\n")
    text = string.gsub(text, "\n\n+", "\n\n")
    return strtrim(text)
end

function Extras:ShowSubtitle(soundData)
    local frame = self:GetSubtitleFrame()
    if not frame then
        return
    end
    local settings = Addon.db.profile.Extras
    if not settings.Subtitles or not soundData or Addon.db.profile.SoundQueueUI.HideFrame then
        frame:Hide()
        return
    end

    local text
    if soundData.isTTS and soundData.ttsSections then
        text = soundData.ttsSections[soundData.ttsSection]
    else
        text = soundData.text
    end
    text = CleanSubtitle(text)
    if text == "" then
        frame:Hide()
        return
    end
    if string.len(text) > settings.SubtitleMaxLength then
        text = string.sub(text, 1, settings.SubtitleMaxLength) .. "..."
    end

    local font, _, flags = GameFontHighlight:GetFont()
    frame.text:SetFont(font, settings.SubtitleFontSize, flags)
    frame.text:SetText(text)
    frame:SetHeight(frame.text:GetStringHeight() + 16)
    frame:Show()
end

function Extras:HideSubtitle()
    if self.subtitle then
        self.subtitle:Hide()
    end
end

------------------------------------------------------------
-- Music ducking

function Extras:DuckMusic()
    local settings = Addon.db.profile.Extras
    if not settings.DuckMusic or Addon.db.global.DuckedMusicVolume then
        return
    end
    local volume = tonumber(GetCVar("Sound_MusicVolume"))
    if not volume then
        return
    end
    -- Remembered in SavedVariables so the volume can be restored after a crash or disconnect
    Addon.db.global.DuckedMusicVolume = volume
    SetCVar("Sound_MusicVolume", volume * settings.DuckMusicLevel)
end

function Extras:RestoreMusic()
    local volume = Addon.db.global.DuckedMusicVolume
    if volume then
        SetCVar("Sound_MusicVolume", volume)
        Addon.db.global.DuckedMusicVolume = nil
    end
end

------------------------------------------------------------
-- Voice previews (/vo voices): plays one sample per race and sex from generated data modules

function Extras:PlayVoicePreviews()
    local queued = 0
    for _, module in DataModules:GetModules() do
        for _, preview in ipairs(module.VoicePreviews or {}) do
            local label = format("%s (%s)", preview.race, preview.sex)
            ---@type SoundData
            local soundData = {
                event = Enums.SoundEvent.QuestAccept,
                questID = 0,
                name = label,
                title = preview.voice,
                text = format("%s - %s|n%s", label, preview.voice, preview.text or ""),
                fileName = "preview:" .. module.METADATA.AddonName .. ":" .. preview.file,
                filePath = format([[Interface\AddOns\%s\%s]], module.METADATA.AddonName,
                    module:GetSoundPath(preview.file, Enums.SoundEvent.QuestAccept)),
                length = preview.length,
                module = module,
                npcSex = preview.sex == "female" and 3 or 2,
            }
            SoundQueue:AddSoundToQueue(soundData, true)
            queued = queued + 1
        end
    end
    if queued == 0 then
        print("|cFF00CCFFVoiceOver:|r no voice previews found. Run Tools\\generate_voices.py once and restart the game.")
    else
        print(format("|cFF00CCFFVoiceOver:|r playing %d voice previews. /vo clear stops them.", queued))
    end
end

------------------------------------------------------------
-- Hooks

function Extras:OnSoundStarted(soundData)
    self:ShowSubtitle(soundData)
    self:DuckMusic()
end

function Extras:OnQueueChanged()
    local current = SoundQueue:GetCurrentSound()
    if not current or Addon.db.char.IsPaused then
        self:HideSubtitle()
        self:RestoreMusic()
    end
end

function Extras:Initialize()
    if self.initialized or not Version.IsAnyRetail then
        return
    end
    self.initialized = true

    -- Restore the music volume if the game was closed while a voiceover was playing
    self:RestoreMusic()

    hooksecurefunc(SoundQueue, "PlaySound", function(_, soundData) Extras:OnSoundStarted(soundData) end)
    hooksecurefunc(SoundQueue, "RemoveSoundFromQueue", function() Extras:OnQueueChanged() end)
    hooksecurefunc(SoundQueue, "PauseQueue", function() Extras:OnQueueChanged() end)
    hooksecurefunc(TTS, "SpeakCurrentSection", function(_, soundData) Extras:ShowSubtitle(soundData) end)

    local events = CreateFrame("Frame")
    events:RegisterEvent("PLAYER_REGEN_DISABLED")
    events:RegisterEvent("PLAYER_REGEN_ENABLED")
    events:RegisterEvent("PLAYER_LOGOUT")
    events:SetScript("OnEvent", function(_, event)
        if event == "PLAYER_LOGOUT" then
            Extras:RestoreMusic()
        elseif event == "PLAYER_REGEN_DISABLED" then
            if Addon.db.profile.Extras.PauseInCombat and not Addon.db.char.IsPaused and not SoundQueue:IsEmpty() then
                SoundQueue:PauseQueue()
                Extras.pausedForCombat = true
            end
        elseif event == "PLAYER_REGEN_ENABLED" then
            if Extras.pausedForCombat then
                Extras.pausedForCombat = false
                if Addon.db.char.IsPaused then
                    SoundQueue:ResumeQueue()
                end
            end
        end
    end)
end
