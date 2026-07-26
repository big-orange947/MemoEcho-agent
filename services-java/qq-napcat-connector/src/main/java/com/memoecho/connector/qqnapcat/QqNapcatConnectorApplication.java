package com.memoecho.connector.qqnapcat;

import com.memoecho.connector.qqnapcat.config.EventCenterProperties;
import com.memoecho.connector.qqnapcat.config.NapcatApiProperties;
import com.memoecho.connector.qqnapcat.config.NapcatWebUiProperties;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;

@SpringBootApplication
@EnableConfigurationProperties({EventCenterProperties.class, NapcatApiProperties.class, NapcatWebUiProperties.class})
public class QqNapcatConnectorApplication {

    public static void main(String[] args) {
        SpringApplication.run(QqNapcatConnectorApplication.class, args);
    }
}
