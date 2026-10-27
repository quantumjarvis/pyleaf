from pyleaf.core import Bean, Configuration


class ScannedBean:
    pass


@Configuration
class ScannedConfig:
    @Bean
    def scanned_bean(self) -> ScannedBean:
        return ScannedBean()
