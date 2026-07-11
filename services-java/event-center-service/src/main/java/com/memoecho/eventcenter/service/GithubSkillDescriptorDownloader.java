package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.model.GithubSkillReference;

public interface GithubSkillDescriptorDownloader {

    String downloadSkillDescriptor(GithubSkillReference reference);
}
