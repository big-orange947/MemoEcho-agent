package com.memoecho.eventcenter.dto;

import com.fasterxml.jackson.databind.JsonNode;

/**
 * QCE 单文件 JSON 的导入请求。
 *
 * <p>桌面端读取用户本地导出的 JSON 后再调用该接口，服务端不会接收或扫描任意本机目录。
 * 这样既避免 Event Center 获得不必要的文件系统权限，也让用户能在导入前明确确认范围。</p>
 */
public record QceImportRequest(
        JsonNode exportData,
        String sourceName,
        String chatIdOverride,
        String chatTypeOverride
) {
}
