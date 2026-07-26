package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.ConversationCognitionCard;

import java.util.Optional;

/** 会话认知卡持久化边界，所有查询都必须携带 userId。 */
public interface ConversationCognitionCardRepository {

    /** 新增认知卡或保存同一 ID 的更新。 */
    ConversationCognitionCard save(ConversationCognitionCard card);

    /** 按用户和会话唯一定位认知卡。 */
    Optional<ConversationCognitionCard> findByScope(
            String userId,
            String platform,
            String chatType,
            String chatId
    );

    /** 删除当前用户指定会话的认知卡。 */
    int deleteByScope(String userId, String platform, String chatType, String chatId);
}
