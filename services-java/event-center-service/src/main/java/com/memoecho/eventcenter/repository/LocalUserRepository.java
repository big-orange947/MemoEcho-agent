package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.LocalUser;

import java.util.Optional;

public interface LocalUserRepository {

    /** 保存本地用户。 */
    LocalUser save(LocalUser user);

    /** 按规范化用户名查询登录账户。 */
    Optional<LocalUser> findByUsername(String username);

    /** 按不可变用户 ID 查询账户。 */
    Optional<LocalUser> findById(String id);
}
