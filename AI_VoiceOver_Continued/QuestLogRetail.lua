setfenv(1, VoiceOver)

-- Play buttons for the modern quest log (QuestMapFrame), used by retail-based
-- clients such as WoW Forever. The classic QuestLogFrame is handled by
-- QuestOverlayUI.lua instead.
--   * a small play/stop button next to every quest title in the list
--   * a larger play/stop button in the quest details view

QuestLogRetail = {
    ---@type table<Frame, Button>
    listButtons = {},
    ---@type table<number, SoundData>
    playing = {},
}

local PLAY_TEXTURE = [[Interface\AddOns\AI_VoiceOver_Continued\Textures\QuestLogPlayButton]]
local STOP_TEXTURE = [[Interface\AddOns\AI_VoiceOver_Continued\Textures\QuestLogStopButton]]

local function GetQuestDescription(questID)
    if not (C_QuestLog and C_QuestLog.GetSelectedQuest and C_QuestLog.SetSelectedQuest and GetQuestLogQuestText) then
        return
    end
    local previous = C_QuestLog.GetSelectedQuest()
    C_QuestLog.SetSelectedQuest(questID)
    local description = GetQuestLogQuestText()
    if previous and previous ~= questID then
        C_QuestLog.SetSelectedQuest(previous)
    end
    return description
end

local function CanPlay(questID)
    return DataModules:PrepareSound({ event = Enums.SoundEvent.QuestAccept, questID = questID }) or TTS:IsEnabled()
end

function QuestLogRetail:IsPlaying(questID)
    local soundData = self.playing[questID]
    return soundData and SoundQueue:Contains(soundData)
end

function QuestLogRetail:Toggle(questID)
    if self:IsPlaying(questID) then
        SoundQueue:RemoveSoundFromQueue(self.playing[questID])
        return
    end

    local type, id = DataModules:GetQuestLogQuestGiverTypeAndID(questID)
    ---@type SoundData
    local soundData = {
        event = Enums.SoundEvent.QuestAccept,
        questID = questID,
        name = id and DataModules:GetObjectName(type, id) or "Quest Log",
        title = C_QuestLog.GetTitleForQuestID and C_QuestLog.GetTitleForQuestID(questID) or nil,
        text = GetQuestDescription(questID),
        unitGUID = id and Enums.GUID:CanHaveID(type) and Utils:MakeGUID(type, id) or nil,
        npcSex = false, -- The quest log has no speaking unit; don't read the current interaction target
        addedCallback = function(soundData) Utils:CreateNPCModelFrame(soundData) end,
        stopCallback = function()
            self.playing[questID] = nil
            self:Refresh()
        end,
    }
    self.playing[questID] = soundData
    SoundQueue:AddSoundToQueue(soundData)
    if not SoundQueue:Contains(soundData) then
        self.playing[questID] = nil
    end
    self:Refresh()
end

local function StyleButton(button, size)
    button:SetSize(size, size)
    button:SetHitRectInsets(2, 2, 2, 2)
    button:SetHighlightTexture([[Interface\BUTTONS\UI-Panel-MinimizeButton-Highlight]])
    button:SetNormalTexture(PLAY_TEXTURE)
    button:SetDisabledTexture(PLAY_TEXTURE)
    button:GetDisabledTexture():SetDesaturated(true)
    button:GetDisabledTexture():SetAlpha(0.33)
    button:SetScript("OnClick", function(self)
        if self.questID then
            QuestLogRetail:Toggle(self.questID)
        end
    end)
    button:SetScript("OnEnter", function(self)
        GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
        GameTooltip:SetText(QuestLogRetail:IsPlaying(self.questID) and "Stop voiceover" or "Play voiceover")
        GameTooltip:Show()
    end)
    button:SetScript("OnLeave", GameTooltip_Hide)
end

local function UpdateButton(button, questID)
    button.questID = questID
    button:SetNormalTexture(QuestLogRetail:IsPlaying(questID) and STOP_TEXTURE or PLAY_TEXTURE)
    button:SetEnabled(CanPlay(questID))
    button:Show()
end

function QuestLogRetail:UpdateList()
    for _, button in pairs(self.listButtons) do
        button:Hide()
    end
    if not Addon.db.profile.QuestLog.ListButtons then
        return
    end
    local pool = QuestScrollFrame and QuestScrollFrame.titleFramePool
    if not pool then
        return
    end
    for titleFrame in pool:EnumerateActive() do
        if titleFrame.questID then
            local button = self.listButtons[titleFrame]
            if not button then
                button = CreateFrame("Button", nil, titleFrame)
                StyleButton(button, 18)
                -- The title text starts 31px from the left edge, leaving room for the button
                button:SetPoint("TOPLEFT", titleFrame, "TOPLEFT", 11, -5)
                self.listButtons[titleFrame] = button
            end
            button:SetFrameLevel(titleFrame:GetFrameLevel() + 5)
            UpdateButton(button, titleFrame.questID)
        end
    end
end

function QuestLogRetail:UpdateDetails()
    local details = QuestMapFrame and QuestMapFrame.DetailsFrame
    if not details then
        return
    end
    if not self.detailsButton then
        self.detailsButton = CreateFrame("Button", nil, details)
        StyleButton(self.detailsButton, 28)
        self.detailsButton:SetPoint("TOPRIGHT", details, "TOPRIGHT", -8, -4)
    end
    local questID = details.questID
    if questID and details:IsShown() then
        self.detailsButton:SetFrameLevel(details:GetFrameLevel() + 10)
        UpdateButton(self.detailsButton, questID)
    else
        self.detailsButton:Hide()
    end
end

function QuestLogRetail:Refresh()
    pcall(self.UpdateList, self)
    pcall(self.UpdateDetails, self)
end

function QuestLogRetail:Initialize()
    if self.initialized or not (Version.IsAnyRetail and QuestMapFrame and not QuestLogFrame) then
        return
    end
    self.initialized = true
    if QuestLogQuests_Update then
        hooksecurefunc("QuestLogQuests_Update", function() QuestLogRetail:Refresh() end)
    end
    if QuestMapFrame_ShowQuestDetails then
        hooksecurefunc("QuestMapFrame_ShowQuestDetails", function() QuestLogRetail:Refresh() end)
    end
    if QuestMapFrame.DetailsFrame then
        QuestMapFrame.DetailsFrame:HookScript("OnHide", function() QuestLogRetail:Refresh() end)
    end
end
