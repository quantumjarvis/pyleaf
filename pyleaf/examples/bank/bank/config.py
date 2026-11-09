"""TxConfig: the explicit ``@EnableTransactionManagement`` declaration.

Transaction management needs no switch — the tx auto-configuration
registers the ``@Transactional`` interceptor whenever pyleaf-tx is
installed and a ``TransactionManager`` bean exists (the starter provides
one as soon as ``app.datasource.url`` is set). This annotation declares
the intent anyway: it stamps a detectable flag and registers the
``transactionManagementMarker`` bean, visible in ``/actuator/beans``
and the condition report.
"""

from pyleaf.core import Configuration
from pyleaf.data import EnableTransactionManagement


@Configuration
@EnableTransactionManagement
class TxConfig:
    """Marker-only configuration — declares transaction management on."""
