setfenv(1, VoiceOver)

---@class Addon : AceAddon, AceAddon-3.0, AceEvent-3.0, AceTimer-3.0
---@field db VoiceOverConfig|AceDBObject-3.0
local AceAddon = LibStub("AceAddon-3.0")
local originalAddon = AceAddon:GetAddon("VoiceOver", true)
if originalAddon and originalAddon.Disable then
    -- Loading both players used to cause a duplicate AceAddon error and two
    -- event handlers. Disable the old player for this session; its folder is
    -- also disabled below for the next login.
    originalAddon:Disable()
end
Addon = AceAddon:NewAddon("VoiceOverContinued", "AceEvent-3.0", "AceTimer-3.0")

Addon.OnAddonLoad = {}
local AUTO_POLL_INTERVAL = 0.1

-- On modern clients, writing to StaticPopupDialogs and calling StaticPopup_Show
-- from an addon taints the shared popup frames, which can surface as
-- "blocked from an action only available to the Blizzard UI". Use a private
-- notice frame there and keep StaticPopup for legacy clients only.
local noticeFrame
function Addon:ShowNotice(text, onAccept)
    if Version.IsAnyLegacy then
        StaticPopupDialogs["VOICEOVER_NOTICE"] =
        {
            text = "%s",
            button1 = OKAY,
            timeout = 0,
            whileDead = 1,
            OnAccept = onAccept,
        }
        StaticPopup_Show("VOICEOVER_NOTICE", text)
        return
    end

    if not noticeFrame then
        noticeFrame = CreateFrame("Frame", "VoiceOverNoticeFrame", UIParent, "BackdropTemplate")
        noticeFrame:SetWidth(360)
        noticeFrame:SetPoint("TOP", 0, -135)
        noticeFrame:SetFrameStrata("DIALOG")
        noticeFrame:SetToplevel(true)
        noticeFrame:EnableMouse(true)
        noticeFrame:SetBackdrop({
            bgFile = [[Interface\DialogFrame\UI-DialogBox-Background]],
            edgeFile = [[Interface\DialogFrame\UI-DialogBox-Border]],
            tile = true, tileSize = 32, edgeSize = 32,
            insets = { left = 11, right = 12, top = 12, bottom = 11 },
        })
        noticeFrame.text = noticeFrame:CreateFontString(nil, "ARTWORK", "GameFontHighlight")
        noticeFrame.text:SetPoint("TOP", 0, -20)
        noticeFrame.text:SetWidth(310)
        noticeFrame.button = CreateFrame("Button", nil, noticeFrame, "UIPanelButtonTemplate")
        noticeFrame.button:SetSize(128, 22)
        noticeFrame.button:SetPoint("BOTTOM", 0, 16)
        noticeFrame.button:SetText(OKAY)
        noticeFrame.button:SetScript("OnClick", function()
            noticeFrame:Hide()
            local callback = noticeFrame.onAccept
            noticeFrame.onAccept = nil
            if callback then callback() end
        end)
    end
    noticeFrame.text:SetText(text)
    noticeFrame.onAccept = onAccept
    noticeFrame:SetHeight(noticeFrame.text:GetStringHeight() + 70)
    noticeFrame:Show()
end

local function IsFrameVisible(frame)
    if not frame then
        return false
    elseif frame.IsVisible then
        return frame:IsVisible()
    end
    return frame:IsShown()
end

local function GetVisibleQuestEvent()
    -- Completion and progress take priority over detail. IsVisible accounts
    -- for hidden parents; IsShown can remain true on an inactive child panel.
    if IsFrameVisible(QuestFrameRewardPanel) then
        return "QUEST_COMPLETE"
    elseif IsFrameVisible(QuestFrameProgressPanel) then
        return "QUEST_PROGRESS"
    elseif IsFrameVisible(QuestFrameDetailPanel) then
        return "QUEST_DETAIL"
    elseif IsFrameVisible(QuestFrameGreetingPanel) then
        return "QUEST_GREETING"
    end

    local questID = GetQuestID and GetQuestID()
    if questID and questID ~= 0 then
        -- Quest-log detail views do not use the NPC QuestFrame panels, but
        -- GetQuestID is authoritative while they are visible.
        return "QUEST_DETAIL"
    end
end

function Addon:ShowMissingDataModulePopup()
    if DataModules:HasRegisteredModules() then
        return
    end

    local loadDetails = {}
    for _, module in DataModules:GetPresentModules() do
        local reason = DataModules:GetModuleLoadError(module.AddonName)
        if reason then
            table.insert(loadDetails, format("%s: %s", module.AddonName, reason))
        end
    end
    local details = next(loadDetails) and ("|n|nDetected but not loaded:|n" .. table.concat(loadDetails, "|n")) or ""
    self:ShowNotice([[VoiceOver Continued|n|nNo usable sound packs were loaded.|n|nKeep "AI_VoiceOverData_Vanilla" installed beside this addon. Run "/vo diagnostics" for details.]] .. details)
end

function Addon:InvokeQuestHandler(event, source)
    local handler = self[event]
    if not handler then
        Debug:Record("handler-missing", format("No handler exists for %s", tostring(event)))
        return false
    end

    Debug:Record("quest-dispatch", format("Dispatching %s through %s", event, source or "manual reader"))
    local succeeded, errorMessage = pcall(handler, self, event)
    if not succeeded then
        Debug:Record("handler-error", format("%s failed: %s", event, tostring(errorMessage)))
        local errorHandler = geterrorhandler and geterrorhandler()
        if errorHandler then
            errorHandler(errorMessage)
        end
        return false
    end
    return true
end

function Addon:ReadVisibleQuest(source)
    local event = GetVisibleQuestEvent()
    if not event then
        Debug:Record("visible-panel-missing", "GetQuestID returned 0 and no Blizzard quest panel is visible")
        return false
    end
    return self:InvokeQuestHandler(event, source or "visible quest reader")
end

---@class VoiceOverConfig
local defaults = {
    profile = {
        SoundQueueUI = {
            LockFrame = false,
            FrameScale = 0.7,
            FrameStrata = "HIGH",
            HidePortrait = false,
            HideFrame = false,
        },
        Audio = {
            GossipFrequency = Enums.GossipFrequency.OncePerQuestNPC,
            SoundChannel = Enums.SoundChannel.Master,
            AutoToggleDialog = Version.IsLegacyVanilla or Version:IsRetailOrAboveLegacyVersion(60100),
            StopAudioOnDisengage = false,
        },
        TTS = {
            Enabled = true,          -- Read lines without a recording through the client's text-to-speech
            Gossip = true,           -- Also read NPC gossip without a recording
            ReadTitle = true,        -- Prepend the quest title when reading a quest
            MaleVoice = nil,         -- voiceID, nil = automatic
            FemaleVoice = nil,       -- voiceID, nil = automatic
            Rate = 0,                -- -10 .. 10
            Volume = 100,            -- 0 .. 100
            CollectMissing = true,   -- Save lines without a recording for Tools\generate_voices.py
            MatchLanguage = false,   -- Ignore recordings that are not in the client's language
        },
        Extras = {
            Subtitles = true,
            SubtitleFontSize = 13,
            SubtitleMaxLength = 900,
            DuckMusic = true,
            DuckMusicLevel = 0.3,    -- Music volume factor while a voiceover plays
            PauseInCombat = true,
        },
        QuestLog = {
            ListButtons = true,      -- Play buttons next to quest titles in the modern quest log
        },
        MinimapButton = {
            LibDBIcon = {}, -- Table used by LibDBIcon to store position (minimapPos), dragging lock (lock) and hidden state (hide)
            Commands = {
                -- References keys from Options.table.args.SlashCommands.args table
                LeftButton = "Options",
                MiddleButton = "PlayPause",
                RightButton = "Clear",
            }
        },
        LegacyWrath = (Version.IsLegacyWrath or Version.IsLegacyBurningCrusade or nil) and {
            PlayOnMusicChannel = {
                Enabled = true,
                Volume = 1,
                FadeOutMusic = 0.5,
            },
            HDModels = false,
        },
        DebugEnabled = false,
    },
    char = {
        IsPaused = false,
        hasSeenGossipForNPC = {},
        RecentQuestTitleToID = Version:IsBelowLegacyVersion(30300) and {},
    }
}

local lastGossipOptions
local selectedGossipOption
local currentQuestSoundData
local currentGossipSoundData

function Addon:OnInitialize()
    self.db = LibStub("AceDB-3.0"):New("VoiceOverDB", defaults)
    self.db.RegisterCallback(self, "OnProfileChanged", "RefreshConfig")
    self.db.RegisterCallback(self, "OnProfileReset", "RefreshConfig")

    SoundQueueUI:Initialize()
    -- Discover data packs now, but load their multi-megabyte generated Lua
    -- tables after entering the world. Keeping LoadAddOn out of AceAddon's
    -- shared initialization/login stack avoids Hardcore's stricter script
    -- time budget being charged to AceAddon-3.0.
    DataModules:EnumerateAddons(false)
    self.dataModulesPending = not DataModules:HasRegisteredModules()
    local function LoadDeferredDataModules()
        if not self.dataModulesPending then
            return
        end
        local succeeded, loadError = pcall(DataModules.LoadPresentModules, DataModules)
        self.dataModulesPending = nil
        if not succeeded then
            self.dataModulesDeferredError = tostring(loadError)
            Debug:Record("data-load-error", self.dataModulesDeferredError)
        elseif DataModules:HasRegisteredModules() then
            Debug:Record("data-ready", "Deferred VoiceOver data modules finished loading")
        end
        self:ShowMissingDataModulePopup()
    end
    local function ScheduleDeferredDataLoad()
        if C_Timer and C_Timer.After then
            C_Timer.After(1, LoadDeferredDataModules)
        else
            self:ScheduleTimer(LoadDeferredDataModules, 1)
        end
    end
    if self.dataModulesPending then
        if IsLoggedIn and IsLoggedIn() then
            ScheduleDeferredDataLoad()
        else
            self.dataLoaderFrame = CreateFrame("Frame")
            self.dataLoaderFrame:RegisterEvent("PLAYER_ENTERING_WORLD")
            self.dataLoaderFrame:SetScript("OnEvent", function(frame)
                frame:UnregisterEvent("PLAYER_ENTERING_WORLD")
                ScheduleDeferredDataLoad()
            end)
        end
    end
    local optionsSucceeded, optionsError = pcall(Options.Initialize, Options)
    if not optionsSucceeded then
        self.optionsInitializationError = tostring(optionsError)
        Debug:Record("options-ui-failed", self.optionsInitializationError)
    end

    -- Install this before all optional event registration. AceAddon deliberately
    -- safe-calls OnInitialize, so a later registration failure must not take
    -- automatic playback down with it.
    Debug:Record("options-ready", "Options and manual quest reader are registered")
    self.autoQuestState = {
        candidateAge = 0,
        retryDelay = 0,
        lastHandledAt = 0,
    }

    local function PollAutomaticQuest()
        local state = self.autoQuestState
        if self.dataModulesPending then
            return
        end
        local questCallSucceeded, questID = pcall(function()
            return GetQuestID and GetQuestID() or 0
        end)
        if not questCallSucceeded then
            error(questID)
        end
        if not questID or questID == 0 then
            state.candidateKey = nil
            state.candidateAge = 0
            state.retryDelay = 0
            state.completedKey = nil
            return
        end

        local titleSucceeded, questTitle = pcall(function()
            return GetTitleText and GetTitleText() or ""
        end)
        if not titleSucceeded then
            error(questTitle)
        end
        local eventSucceeded, event = pcall(GetVisibleQuestEvent)
        if not eventSucceeded then
            error(event)
        end
        event = event or "QUEST_DETAIL"
        local key = format("%s:%s:%s", event, tostring(questID), questTitle or "")
        if key ~= state.candidateKey then
            state.candidateKey = key
            state.candidateAge = 0
            state.retryDelay = 0
            state.completedKey = nil
            Debug:Record("auto-watcher-stabilizing", format("Waiting for %s quest %s to finish populating", event,
                tostring(questID)))
            return
        end

        -- A synchronous Classic quest event can expose the previous quest's
        -- globals briefly while switching NPCs. Do not replay the most recent
        -- quest key during that transition; /vo read remains an explicit way
        -- to replay it.
        if key == state.lastHandledKey and GetTime() - state.lastHandledAt < 5 then
            state.completedKey = key
            return
        end

        state.candidateAge = state.candidateAge + AUTO_POLL_INTERVAL
        state.retryDelay = math.max(0, state.retryDelay - AUTO_POLL_INTERVAL)
        local expectedSoundEvent = event == "QUEST_DETAIL" and Enums.SoundEvent.QuestAccept or
            event == "QUEST_PROGRESS" and Enums.SoundEvent.QuestProgress or
            event == "QUEST_COMPLETE" and Enums.SoundEvent.QuestComplete
        for _, queuedSound in ipairs(SoundQueue.sounds) do
            if queuedSound.questID == questID and queuedSound.event == expectedSoundEvent then
                state.completedKey = key
                state.lastHandledKey = key
                state.lastHandledAt = GetTime()
                return
            end
        end

        if key ~= state.completedKey and state.candidateAge >= 0.4 and state.retryDelay <= 0 then
            state.retryDelay = 0.5
            Debug:Record("auto-watcher-dispatch", format("Automatically reading %s quest %s", event,
                tostring(questID)))
            self:ReadVisibleQuest("automatic GetQuestID timer")

            local stage = Debug.runtime and Debug.runtime.stage
            if stage == "queued" or stage == "queue-paused" or stage == "playing" or
               stage == "sound-disabled" or stage == "file-playback-failed" or stage == "playback-failed" or
               stage == "data-lookup-failed" then
                state.completedKey = key
                state.lastHandledKey = key
                state.lastHandledAt = GetTime()
            end
        end
    end

    local tickerInstalled, tickerOrError = pcall(self.ScheduleRepeatingTimer, self, function()
        local succeeded, pollError = pcall(PollAutomaticQuest)
        if not succeeded then
            Debug:Record("auto-watcher-error", tostring(pollError))
        end
    end, AUTO_POLL_INTERVAL)
    if tickerInstalled then
        self.autoQuestTicker = tickerOrError
        Debug:Record("auto-watcher-ready", "AceTimer GetQuestID polling is active")
    else
        Debug:Record("auto-watcher-install-failed", tostring(tickerOrError))
    end

    local slashInstalled, slashError = pcall(function()
        _G.SLASH_VOICEOVERCONTINUEDREAD1 = "/voread"
        _G.SlashCmdList.VOICEOVERCONTINUEDREAD = function()
            self:ReadVisibleQuest("/voread")
        end
    end)
    if not slashInstalled then
        Debug:Record("standalone-slash-failed", tostring(slashError))
    end

    self.eventBridgeErrors = {}
    local aceRegistered, aceError = pcall(self.RegisterEvent, self, "ADDON_LOADED")
    if not aceRegistered then
        table.insert(self.eventBridgeErrors, "AceEvent ADDON_LOADED: " .. tostring(aceError))
    end

    -- Quest detail/progress/completion are intentionally absent here. On
    -- Classic Era those synchronous events can fire before GetQuestID and the
    -- text globals change, replaying the previous quest. The stabilized 10 Hz
    -- watcher above is their single automatic dispatcher.
    local directEvents = {
        "QUEST_GREETING",
        "QUEST_FINISHED",
        "GOSSIP_SHOW",
        "GOSSIP_CLOSED",
    }
    local directEventLookup = {}
    local lastDispatch = {}
    local dispatching = {}
    local function DispatchDirectEvent(event, source)
        local now = GetTime()
        if dispatching[event] or lastDispatch[event] and now - lastDispatch[event] < 0.5 then
            return
        end

        local handler = self[event]
        if handler then
            dispatching[event] = true
            Debug:Record("event-dispatch", format("Dispatching %s through %s", event, source))
            local succeeded, errorMessage = pcall(handler, self, event)
            dispatching[event] = nil

            if not succeeded then
                Debug:Record("handler-error", format("%s failed: %s", event, tostring(errorMessage)))
                local errorHandler = geterrorhandler and geterrorhandler()
                if errorHandler then
                    errorHandler(errorMessage)
                end
                return
            end

            -- Only suppress the panel/watcher fallback when this route
            -- actually reached the queue or playback stage. An early event
            -- with unpopulated quest globals must be allowed to retry.
            local stage = Debug.runtime and Debug.runtime.stage
            if stage == "queued" or stage == "queue-paused" or stage == "playing" then
                lastDispatch[event] = GetTime()
            else
                lastDispatch[event] = nil
            end
        end
    end
    self.DispatchDirectEvent = function(addon, event, source)
        return DispatchDirectEvent(event, source or "manual dispatch")
    end

    -- Greeting globals can have the same synchronous-event race. Coalesce the
    -- event/frame signals and read them on a later frame after Blizzard has
    -- populated the new NPC and text.
    local deferredSpeechEvents = {
        QUEST_GREETING = true,
        GOSSIP_SHOW = true,
    }
    local deferredEventGeneration = {}
    local function SignalDirectEvent(event, source)
        if event == "QUEST_FINISHED" then
            deferredEventGeneration.QUEST_GREETING = (deferredEventGeneration.QUEST_GREETING or 0) + 1
        elseif event == "GOSSIP_CLOSED" then
            deferredEventGeneration.GOSSIP_SHOW = (deferredEventGeneration.GOSSIP_SHOW or 0) + 1
        end
        if not deferredSpeechEvents[event] then
            DispatchDirectEvent(event, source)
            return
        end
        local generation = (deferredEventGeneration[event] or 0) + 1
        deferredEventGeneration[event] = generation
        self:ScheduleTimer(function()
            if deferredEventGeneration[event] == generation then
                DispatchDirectEvent(event, source .. " (deferred)")
            end
        end, 0.1)
    end

    self.directEventFrame = CreateFrame("Frame")
    for _, event in ipairs(directEvents) do
        local registered, registerError = pcall(self.directEventFrame.RegisterEvent, self.directEventFrame, event)
        if registered then
            directEventLookup[event] = true
        else
            table.insert(self.eventBridgeErrors, format("Direct %s: %s", event, tostring(registerError)))
        end
    end
    self.directEventFrame:SetScript("OnEvent", function(frame, event)
        SignalDirectEvent(event, "direct frame")
    end)

    -- Keep UI fallbacks only for NPC greetings. Quest narration is exclusively
    -- handled by the stabilized watcher.
    local panelEvents = {
        { frame = QuestFrameGreetingPanel, event = "QUEST_GREETING" },
        { frame = GossipFrame, event = "GOSSIP_SHOW" },
    }
    for _, binding in ipairs(panelEvents) do
        if binding.frame and binding.frame.HookScript then
            local event = binding.event
            local hooked, hookError = pcall(binding.frame.HookScript, binding.frame, "OnShow", function()
                    SignalDirectEvent(event, "panel OnShow")
                end)
            if not hooked then
                table.insert(self.eventBridgeErrors, format("Panel %s: %s", event, tostring(hookError)))
            end
        end
    end

    -- Hook the exact Blizzard dispatcher that updates the visible quest
    -- panels. This runs after Blizzard has populated GetQuestID/GetTitleText.
    if QuestFrame_OnEvent and hooksecurefunc then
        local hooked, hookError = pcall(hooksecurefunc, "QuestFrame_OnEvent", function(frame, event)
                if directEventLookup[event] then
                    SignalDirectEvent(event, "QuestFrame_OnEvent hook")
                end
            end)
        if not hooked then
            table.insert(self.eventBridgeErrors, "QuestFrame_OnEvent: " .. tostring(hookError))
        end
    end

    if next(self.eventBridgeErrors) then
        Debug:Record("event-bridge-partial", format("Quest watcher is active; %d optional bridge component(s) failed",
            getn(self.eventBridgeErrors)))
    else
        Debug:Record("event-bridge-ready", "Stabilized quest watcher and deferred greeting bridge are registered")
    end

    -- Modern clients return a name from GetAddOnInfo even for addons that are
    -- not installed, so only react when the old player was actually loaded.
    if originalAddon or IsAddOnLoaded("AI_VoiceOver") then
        DisableAddOn("AI_VoiceOver")
        if not self.db.profile.SeenContinuedDuplicateDialog then
            self:ShowNotice([[VoiceOver Continued|n|nThe original "AI_VoiceOver" player was also enabled. It has been disabled for the next login, and its event handler was stopped for this session.|n|nKeep "AI_VoiceOverData_Vanilla" enabled; that is the sound pack. You can delete or leave the old player disabled, then /reload.]], function()
                self.db.profile.SeenContinuedDuplicateDialog = true
            end)
        end
    end

    local function MakeAbandonQuestHook(field, getFieldData)
        return function()
            local data = getFieldData()
            local soundsToRemove = {}
            for _, soundData in pairs(SoundQueue.sounds) do
                if Enums.SoundEvent:IsQuestEvent(soundData.event) and soundData[field] == data then
                    table.insert(soundsToRemove, soundData)
                end
            end

            for _, soundData in pairs(soundsToRemove) do
                SoundQueue:RemoveSoundFromQueue(soundData)
            end
        end
    end
    if C_QuestLog and C_QuestLog.AbandonQuest then
        hooksecurefunc(C_QuestLog, "AbandonQuest", MakeAbandonQuestHook("questID", function() return C_QuestLog.GetAbandonQuest() end))
    elseif AbandonQuest then
        hooksecurefunc("AbandonQuest", MakeAbandonQuestHook("questName", function() return GetAbandonQuestName() end))
    end

    if QuestLog_Update then
        hooksecurefunc("QuestLog_Update", function()
            QuestOverlayUI:Update()
        end)
    end

    for name, module in pairs({ QuestLogRetail = QuestLogRetail, Extras = Extras }) do
        local succeeded, initError = pcall(module.Initialize, module)
        if not succeeded then
            Debug:Record("init-failed", format("%s: %s", name, tostring(initError)))
        end
    end

    if C_GossipInfo and C_GossipInfo.SelectOption then
        hooksecurefunc(C_GossipInfo, "SelectOption", function(optionID)
            if lastGossipOptions then
                for _, info in ipairs(lastGossipOptions) do
                    if info.gossipOptionID == optionID then
                        selectedGossipOption = info.name
                        break
                    end
                end
                lastGossipOptions = nil
            end
        end)
    elseif SelectGossipOption then
        hooksecurefunc("SelectGossipOption", function(index)
            if lastGossipOptions then
                selectedGossipOption = lastGossipOptions[1 + (index - 1) * 2]
                lastGossipOptions = nil
            end
        end)
    end
end

function Addon:RefreshConfig()
    SoundQueueUI:RefreshConfig()
end

function Addon:ADDON_LOADED(event, addon)
    addon = addon or arg1 -- Thanks, Ace3v...
    local hook = self.OnAddonLoad[addon]
    if hook then
        hook()
    end
end

local function GossipSoundDataAdded(soundData)
    Utils:CreateNPCModelFrame(soundData)

    -- Save current gossip sound data for dialog/frame sync option
    currentGossipSoundData = soundData
end

local function QuestSoundDataAdded(soundData)
    Utils:CreateNPCModelFrame(soundData)

    -- Save current quest sound data for dialog/frame sync option
    currentQuestSoundData = soundData
end

local GetTitleText = GetTitleText -- Store original function before EQL3 (Extended Quest Log 3) overrides it and starts prepending quest level

local function ResolveQuestID(source, questID, questTitle, targetName, questText)
    if questID and questID ~= 0 then
        return questID
    end

    -- On current Classic clients GetQuestID can briefly return 0 while the
    -- QUEST_DETAIL frame is already populated. Resolve it through the loaded
    -- data module's title/NPC/text index instead of silently abandoning the
    -- event. This also handles ambiguous repeated quest titles.
    local fallbackID = DataModules:GetQuestID(source, questTitle or "", targetName or "", questText or "")
    if fallbackID then
        Debug:Record("quest-id-fallback", format("Resolved %q to quest ID %d", questTitle or "", fallbackID))
        return fallbackID
    end

    Debug:Record("quest-id-missing", format("The client returned quest ID 0 and the data module could not resolve %q",
        questTitle or ""))
end

function Addon:QUEST_DETAIL()
    local questID = GetQuestID()
    local questTitle = GetTitleText()
    local questText = GetQuestText()
    local guid = Utils:GetNPCGUID()
    local targetName = Utils:GetNPCName()

    Debug:Record("quest-detail", format("QUEST_DETAIL: raw ID %s, title %q, NPC %q",
        tostring(questID or "nil"), questTitle or "", targetName or ""))
    questID = ResolveQuestID("accept", questID, questTitle, targetName, questText)
    if not questID then
        -- Unknown quest: still narrate it through text-to-speech when enabled
        if not TTS:IsEnabled() then
            return
        end
        questID = 0
    end

    if Addon.db.char.RecentQuestTitleToID and questID ~= 0 then
        Addon.db.char.RecentQuestTitleToID[questTitle] = questID
    end

    local type = guid and Utils:GetGUIDType(guid)
    if type == Enums.GUID.Item then
        -- Allow quests started from items to have VO, book icon will be displayed for them
    elseif not type or not Enums.GUID:CanHaveID(type) then
        -- If the quest is started by something that we cannot extract the ID of (e.g. Player, when sharing a quest) - try to fallback to a questgiver from a module's database
        local id
        type, id = DataModules:GetQuestLogQuestGiverTypeAndID(questID)
        guid = id and Enums.GUID:CanHaveID(type) and Utils:MakeGUID(type, id) or guid
        targetName = id and DataModules:GetObjectName(type, id) or targetName or "Unknown Name"
    end

    -- print("QUEST_DETAIL", questID, questTitle);
    ---@type SoundData
    local soundData = {
        event = Enums.SoundEvent.QuestAccept,
        questID = questID,
        name = targetName,
        title = questTitle,
        text = questText,
        unitGUID = guid,
        unitIsObjectOrItem = Utils:IsNPCObjectOrItem(),
        addedCallback = QuestSoundDataAdded,
    }
    SoundQueue:AddSoundToQueue(soundData)
end

function Addon:QUEST_PROGRESS()
    local questID = GetQuestID()
    local questTitle = GetTitleText()
    local questText = GetProgressText()
    local guid = Utils:GetNPCGUID()
    local targetName = Utils:GetNPCName()

    Debug:Record("quest-progress", format("QUEST_PROGRESS: raw ID %s, title %q, NPC %q",
        tostring(questID or "nil"), questTitle or "", targetName or ""))
    questID = ResolveQuestID("progress", questID, questTitle, targetName, questText)
    if not questID then
        -- Unknown quest: still narrate it through text-to-speech when enabled
        if not TTS:IsEnabled() then
            return
        end
        questID = 0
    end

    if Addon.db.char.RecentQuestTitleToID then
        Addon.db.char.RecentQuestTitleToID[questTitle] = questID
    end

    ---@type SoundData
    local soundData = {
        event = Enums.SoundEvent.QuestProgress,
        questID = questID,
        name = targetName,
        title = questTitle,
        text = questText,
        unitGUID = guid,
        unitIsObjectOrItem = Utils:IsNPCObjectOrItem(),
        addedCallback = QuestSoundDataAdded,
    }
    SoundQueue:AddSoundToQueue(soundData)
end

function Addon:QUEST_COMPLETE()
    local questID = GetQuestID()
    local questTitle = GetTitleText()
    local questText = GetRewardText()
    local guid = Utils:GetNPCGUID()
    local targetName = Utils:GetNPCName()

    Debug:Record("quest-complete", format("QUEST_COMPLETE: raw ID %s, title %q, NPC %q",
        tostring(questID or "nil"), questTitle or "", targetName or ""))
    questID = ResolveQuestID("complete", questID, questTitle, targetName, questText)
    if not questID then
        -- Unknown quest: still narrate it through text-to-speech when enabled
        if not TTS:IsEnabled() then
            return
        end
        questID = 0
    end

    if Addon.db.char.RecentQuestTitleToID and questID ~= 0 then
        Addon.db.char.RecentQuestTitleToID[questTitle] = questID
    end

    -- print("QUEST_COMPLETE", questID, questTitle);
    ---@type SoundData
    local soundData = {
        event = Enums.SoundEvent.QuestComplete,
        questID = questID,
        name = targetName,
        title = questTitle,
        text = questText,
        unitGUID = guid,
        unitIsObjectOrItem = Utils:IsNPCObjectOrItem(),
        addedCallback = QuestSoundDataAdded,
    }
    SoundQueue:AddSoundToQueue(soundData)
end

function Addon:ShouldPlayGossip(guid, text)
    local npcKey = guid or "unknown"

    local gossipSeenForNPC = self.db.char.hasSeenGossipForNPC[npcKey]

    if self.db.profile.Audio.GossipFrequency == Enums.GossipFrequency.OncePerQuestNPC then
        local numActiveQuests = GetNumGossipActiveQuests()
        local numAvailableQuests = GetNumGossipAvailableQuests()
        local npcHasQuests = (numActiveQuests > 0 or numAvailableQuests > 0)
        if npcHasQuests and gossipSeenForNPC then
            return
        end
    elseif self.db.profile.Audio.GossipFrequency == Enums.GossipFrequency.OncePerNPC then
        if gossipSeenForNPC then
            return
        end
    elseif self.db.profile.Audio.GossipFrequency == Enums.GossipFrequency.Never then
        return
    end

    return true, npcKey
end

function Addon:QUEST_GREETING()
    local guid = Utils:GetNPCGUID()
    local targetName = Utils:GetNPCName()
    local greetingText = GetGreetingText()

    -- Can happen if the player interacted with an NPC while having main menu or options opened
    if not guid and not targetName then
        return
    end

    local play, npcKey = self:ShouldPlayGossip(guid, greetingText)
    if not play then
        return
    end

    -- Play the gossip sound
    ---@type SoundData
    local soundData = {
        event = Enums.SoundEvent.QuestGreeting,
        name = targetName,
        text = greetingText,
        unitGUID = guid,
        unitIsObjectOrItem = Utils:IsNPCObjectOrItem(),
        addedCallback = GossipSoundDataAdded,
        startCallback = function()
            self.db.char.hasSeenGossipForNPC[npcKey] = true
        end
    }
    SoundQueue:AddSoundToQueue(soundData)
end

function Addon:GOSSIP_SHOW()
    local guid = Utils:GetNPCGUID()
    local targetName = Utils:GetNPCName()
    local gossipText = GetGossipText()

    -- Can happen if the player interacted with an NPC while having main menu or options opened
    if not guid and not targetName then
        return
    end

    local play, npcKey = self:ShouldPlayGossip(guid, gossipText)
    if not play then
        return
    end

    -- Play the gossip sound
    ---@type SoundData
    local soundData = {
        event = Enums.SoundEvent.Gossip,
        name = targetName,
        title = selectedGossipOption and format([["%s"]], selectedGossipOption),
        text = gossipText,
        unitGUID = guid,
        unitIsObjectOrItem = Utils:IsNPCObjectOrItem(),
        addedCallback = GossipSoundDataAdded,
        startCallback = function()
            self.db.char.hasSeenGossipForNPC[npcKey] = true
        end
    }
    SoundQueue:AddSoundToQueue(soundData)

    selectedGossipOption = nil
    lastGossipOptions = nil
    if C_GossipInfo and C_GossipInfo.GetOptions then
        lastGossipOptions = C_GossipInfo.GetOptions()
    elseif GetGossipOptions then
        lastGossipOptions = { GetGossipOptions() }
    end
end

function Addon:QUEST_FINISHED()
    if Addon.db.profile.Audio.StopAudioOnDisengage and currentQuestSoundData then
        SoundQueue:RemoveSoundFromQueue(currentQuestSoundData)
    end
    currentQuestSoundData = nil
end

function Addon:GOSSIP_CLOSED()
    if Addon.db.profile.Audio.StopAudioOnDisengage and currentGossipSoundData then
        SoundQueue:RemoveSoundFromQueue(currentGossipSoundData)
    end
    currentGossipSoundData = nil

    selectedGossipOption = nil
end
