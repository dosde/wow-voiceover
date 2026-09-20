setfenv(1, VoiceOver)
Debug = {}

Debug.runtime = {
    stage = "addon-loaded",
    message = "Waiting for a quest or gossip event",
}

function Debug:Record(stage, message)
    self.runtime.stage = stage
    self.runtime.message = message
    self.runtime.time = GetTime and GetTime() or 0
    self:Print(message, stage)
end

function Debug:GetRuntimeStatus()
    return self.runtime.stage, self.runtime.message, self.runtime.time
end

function Debug:Print(msg, header)
    if Addon and Addon.db and Addon.db.profile.DebugEnabled then
        if header then
            print(Utils:ColorizeText("VoiceOver", NORMAL_FONT_COLOR_CODE) ..
                Utils:ColorizeText(" (" .. header .. ")", GRAY_FONT_COLOR_CODE) ..
                " - " .. msg)
        else
            print(Utils:ColorizeText("VoiceOver", NORMAL_FONT_COLOR_CODE) ..
                " - " .. msg)
        end
    end
end
