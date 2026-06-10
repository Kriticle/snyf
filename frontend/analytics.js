(function () {
    const CONFIG = {
        projectToken: 'phc_xfJVHa5tFJJHJPBu8axMa7pcuwMALU7rhEzngXCaHmwd',
        apiHost: 'https://us.i.posthog.com',
        defaults: '2026-01-30',
    };

    const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '']);
    let initialized = false;

    function configuredToken() {
        const metaToken = document.querySelector('meta[name="posthog-token"]')?.content;
        return window.SNYF_POSTHOG_TOKEN || metaToken || CONFIG.projectToken;
    }

    function shouldRun() {
        if (!configuredToken()) return false;
        if (window.SNYF_ENABLE_ANALYTICS_LOCAL === true) return true;
        return !LOCAL_HOSTS.has(window.location.hostname);
    }

    function loadPostHog(token) {
        if (initialized || window.posthog?.__loaded) return;
        !function(t,e){var o,n,p,r;e.__SV||(window.posthog=e,e._i=[],e.init=function(i,s,a){function g(t,e){var o=e.split(".");2==o.length&&(t=t[o[0]],e=o[1]),t[e]=function(){t.push([e].concat(Array.prototype.slice.call(arguments,0)))}}(p=t.createElement("script")).type="text/javascript",p.crossOrigin="anonymous",p.async=!0,p.src=s.api_host.replace(".i.posthog.com","-assets.i.posthog.com")+"/static/array.js",(r=t.getElementsByTagName("script")[0]).parentNode.insertBefore(p,r);var u=e;for(void 0!==a?u=e[a]=[]:a="posthog",u.people=u.people||[],u.toString=function(t){var e="posthog";return"posthog"!==a&&(e+="."+a),t||(e+=" (stub)"),e},u.people.toString=function(){return u.toString(1)+".people (stub)"},o="init capture register register_once register_for_session unregister unregister_for_session getFeatureFlag getFeatureFlagPayload isFeatureEnabled reloadFeatureFlags updateEarlyAccessFeatureEnrollment getEarlyAccessFeatures on onFeatureFlags onSessionId getSurveys getActiveMatchingSurveys renderSurvey canRenderSurvey getNextSurveyStep identify setPersonProperties group resetGroups setPersonPropertiesForFlags resetPersonPropertiesForFlags reset get_distinct_id getGroups get_session_id get_session_replay_url alias set_config startSessionRecording stopSessionRecording sessionRecordingStarted captureException loadToolbar get_property getSessionProperty createPersonProfile opt_in_capturing opt_out_capturing has_opted_in_capturing has_opted_out_capturing clear_opt_in_out_capturing debug".split(" "),n=0;n<o.length;n++)g(u,o[n]);e._i.push([i,s,a])},e.__SV=1)}(document,window.posthog||[]);
        window.posthog.init(token, {
            api_host: CONFIG.apiHost,
            defaults: CONFIG.defaults,
            capture_pageview: true,
            persistence: 'localStorage+cookie',
        });
        initialized = true;
    }

    function init() {
        if (!shouldRun()) return false;
        loadPostHog(configuredToken());
        return true;
    }

    function capture(eventName, properties = {}) {
        if (!init()) return;
        window.posthog.capture(eventName, {
            page: window.location.pathname.split('/').pop() || 'index.html',
            ...properties,
        });
    }

    function identify(customer) {
        if (!customer?.customer_id || !init()) return;
        window.posthog.identify(customer.customer_id, {
            email: customer.email,
            plan: customer.plan,
            status: customer.status,
        });
    }

    function reset() {
        if (window.posthog) window.posthog.reset();
    }

    window.snyfAnalytics = { init, capture, identify, reset };
    init();
}());
