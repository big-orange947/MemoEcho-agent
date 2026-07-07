package com.memoecho.connector.qqnapcat.service;

import com.memoecho.connector.qqnapcat.dto.NapcatApiResponse;
import com.memoecho.connector.qqnapcat.dto.NapcatLoginInfoData;
import com.memoecho.connector.qqnapcat.dto.NapcatStatusData;
import org.springframework.stereotype.Service;

import java.util.Map;

@Service
public class NapcatSystemService {

    private final NapcatApiClient apiClient;

    public NapcatSystemService(NapcatApiClient apiClient) {
        this.apiClient = apiClient;
    }

    public NapcatApiResponse<NapcatLoginInfoData> getLoginInfo() {
        // 登录信息主要给联调和健康检查用，方便确认当前挂载的是哪个 QQ 账号。
        return apiClient.call("get_login_info", Map.of(), NapcatLoginInfoData.class);
    }

    public NapcatApiResponse<NapcatStatusData> getStatus() {
        return apiClient.call("get_status", Map.of(), NapcatStatusData.class);
    }
}
