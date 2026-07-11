package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.ConversationProfile;
import java.util.Comparator;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentMap;

public class InMemoryConversationProfileRepository implements ConversationProfileRepository {

    private final ConcurrentMap<String, ConversationProfile> profiles = new ConcurrentHashMap<>();

    @Override
    public ConversationProfile save(ConversationProfile profile) {
        profiles.put(profile.id(), profile);
        return profile;
    }

    @Override
    public Optional<ConversationProfile> findById(String profileId) {
        return Optional.ofNullable(profiles.get(profileId));
    }

    @Override
    public List<ConversationProfile> findAll() {
        return profiles.values().stream()
                .sorted(Comparator
                        .comparingInt(ConversationProfile::priority).reversed()
                        .thenComparing(ConversationProfile::updatedAt, Comparator.reverseOrder()))
                .toList();
    }

    @Override
    public void deleteById(String profileId) {
        profiles.remove(profileId);
    }
}
