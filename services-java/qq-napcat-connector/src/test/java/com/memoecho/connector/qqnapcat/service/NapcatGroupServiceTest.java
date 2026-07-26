package com.memoecho.connector.qqnapcat.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.memoecho.connector.qqnapcat.dto.GroupOperationRequest;
import com.memoecho.connector.qqnapcat.dto.NapcatApiResponse;
import org.junit.jupiter.api.Test;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class NapcatGroupServiceTest {

    @Test
    void shouldMapMuteOperationToFixedNapcatAction() {
        NapcatApiClient apiClient = mock(NapcatApiClient.class);
        NapcatGroupService service = new NapcatGroupService(apiClient);
        NapcatApiResponse<JsonNode> success = new NapcatApiResponse<>("ok", 0, null, null, null, null);
        Map<String, Object> expectedPayload = Map.of(
                "group_id", 1098307542L,
                "user_id", 3807050597L,
                "duration", 600
        );
        when(apiClient.call("set_group_ban", expectedPayload, JsonNode.class)).thenReturn(success);

        NapcatApiResponse<JsonNode> result = service.executeOperation(new GroupOperationRequest(
                "mute_member", 1098307542L, 3807050597L, 600,
                null, null, null, null
        ));

        assertThat(result.status()).isEqualTo("ok");
        verify(apiClient).call("set_group_ban", expectedPayload, JsonNode.class);
    }

    @Test
    void shouldRejectUnknownActionWithoutCallingNapcat() {
        NapcatApiClient apiClient = mock(NapcatApiClient.class);
        NapcatGroupService service = new NapcatGroupService(apiClient);

        NapcatApiResponse<JsonNode> result = service.executeOperation(new GroupOperationRequest(
                "set_group_leave", 1098307542L, null, null,
                null, null, null, null
        ));

        assertThat(result.status()).isEqualTo("failed");
        assertThat(result.message()).contains("Unsupported group operation");
        verify(apiClient, never()).call(eq("set_group_leave"), org.mockito.ArgumentMatchers.any(), eq(JsonNode.class));
    }
}
