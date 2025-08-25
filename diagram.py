
from diagrams import Diagram, Cluster, Edge
from diagrams.onprem.network import Internet
from diagrams import Diagram, Cluster, Edge
from diagrams.onprem.client import Users
from diagrams.onprem.compute import Server
from diagrams.onprem.inmemory import Redis
from diagrams.onprem.network import Nginx
from diagrams.onprem.database import Postgresql
from diagrams.onprem.monitoring import Prometheus, Grafana

with Diagram(
    "Oncology Pilot - Production-Ready Architecture",
    show=False,
    direction="LR",
    filename="pilot_architecture",
    outformat="png",
    graph_attr={"splines":"spline","pad":"0.3","nodesep":"0.4","ranksep":"0.5","fontsize":"10","labeljust":"l"},
):

    ext_hl7 = Internet("HL7 v2 Feeds\n(Labs/ADT)")
    ext_fhir = Internet("FHIR Server\n(*via proxy)")
    ext_fda  = Internet("FDA API\n(*via proxy)")
    ext_wear = Internet("Wearables Vendor\n(*via proxy)")

    with Cluster("Hospital Network (On‑Prem, No PHI in Cloud)"):

        with Cluster("Security & Networking"):
            ingress_gw   = Nginx("Internal Gateway\n(mTLS + RBAC)")
            egress_proxy = Nginx("Egress Proxy\n(all outbound via proxy)")
            policy       = Server("Policy Engine (OPA)")

        with Cluster("Streaming & Processing"):
            ingestor_hl7      = Server("HL7 Ingestor\n(idempotent)")
            redis_streams     = Redis("Redis Streams\n(AOF, TLS)")
            normalizer        = Server("Normalizer\n(EventCommon JSON)")
            qc_dedupe         = Server("QC + Dedupe\n(UCUM, parse tolerant)")
            dlq               = Redis("DLQ\n(replayable)")
            correlator_rules  = Server("Correlator +\nRules Engine")

        with Cluster("State (Read Path <100ms)"):
            redis_state  = Redis("Redis State\n(patient:{key}, series)")
            redis_replica= Redis("Redis Replica\n(Sentinel)")

        with Cluster("API & UI"):
            api  = Server("FastAPI\n/insights, /patients\nSSE/WebSocket")
            ui   = Server("Clinical UI (web)\nES/EN, A11y")
            kiosk= Server("Patient Kiosk / Caregiver\nES/EN, font large")
            clinicians = Users("Oncologists\n& Nurses")

        with Cluster("Async Enrichment (Non‑blocking)"):
            fhir_fetcher = Server("FHIR Fetcher (async)\nXML→JSON, paging")
            fda_fetcher  = Server("FDA Fetcher (async)\nETag/TTL")
            local_cache  = Redis("Local Cache\n(FDA/FHIR)")
            rag          = Server("RAG (curated corpus)\nASCO/NCCN/etc.")
            llm          = Server("AI Assistant (On‑Prem LLM)\nAssistive, Non‑decisive")

        with Cluster("Audit & Observability"):
            audit_sink = Server("Audit Sink\n(append-only, chained)")
            audit_db   = Postgresql("Audit Log DB\n(Postgres WORM/NAS)")
            prom       = Prometheus("Prometheus")
            graf       = Grafana("Grafana")

        with Cluster("Synthetic/De‑ID"):
            synth_gen   = Server("Synthetic Data Generator\n(seed scenarios)")
            deidentifier= Server("De‑ID/Masking\nrule‑based")


        ext_hl7 >> Edge(label="HL7 v2") >> ingestor_hl7 >> Edge(label="XADD hl7:raw") >> redis_streams
        redis_streams >> Edge(label="XREADGROUP ok") >> normalizer
        normalizer >> Edge(label="valid") >> qc_dedupe
        normalizer >> Edge(label="invalid → dlq") >> dlq
        dlq >> Edge(style="dashed", label="replay") >> normalizer
        qc_dedupe >> Edge(label="enriched events") >> correlator_rules >> Edge(label="precompute") >> redis_state
        redis_state >> Edge(style="dotted", label="replicate") >> redis_replica

        ui << Edge(label="HTTPS") << ingress_gw >> Edge(label="mTLS") >> api >> Edge(label="HASH/ZSET") >> redis_state
        kiosk >> Edge(label="HTTPS") >> ingress_gw
        api >> Edge(style="dashed", label="SSE/WebSocket") >> ui
        clinicians >> Edge(style="dotted", label="Use UI") >> ui
        policy >> Edge(style="dotted", label="authz") >> ingress_gw

        fhir_fetcher >> Edge(label="via proxy") >> egress_proxy >> ext_fhir
        fda_fetcher  >> Edge(label="via proxy") >> egress_proxy >> ext_fda
        ext_wear     >> Edge(label="via proxy") >> egress_proxy >> fhir_fetcher

        fhir_fetcher >> Edge(label="cache/set") >> local_cache
        fda_fetcher  >> Edge(label="cache/set") >> local_cache
        local_cache  >> Edge(label="hydrate (async)") >> redis_state

        synth_gen >> Edge(label="seed events") >> redis_streams
        deidentifier >> Edge(label="de‑ID rules") >> synth_gen

        rag >> Edge(label="context") >> llm
        llm >> Edge(style="dashed", label="insights (assistive)\nnot diagnostic") >> api

        for src in [ui, kiosk, api, redis_state, correlator_rules, normalizer, ingestor_hl7, fhir_fetcher, fda_fetcher, llm, egress_proxy, ingress_gw]:
            src >> Edge(color="gray", style="dotted", label="audit") >> audit_sink
        audit_sink >> audit_db

        for obs_src in [api, correlator_rules, normalizer, ingestor_hl7, redis_streams, redis_state, fhir_fetcher, fda_fetcher, llm, egress_proxy, ingress_gw]:
            obs_src >> Edge(color="gray", style="dotted", label="metrics") >> prom
        prom >> graf
