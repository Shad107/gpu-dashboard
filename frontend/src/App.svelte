<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import Header from "./components/Header.svelte";
  import Cards from "./components/Cards.svelte";
  import CoolingChart from "./components/CoolingChart.svelte";
  import PowerChart from "./components/PowerChart.svelte";
  import SettingsModal from "./components/SettingsModal.svelte";
  import Toast from "./components/Toast.svelte";
  import { live } from "./lib/stores.svelte";
  import { i18n } from "./lib/i18n/index.svelte";

  onMount(() => {
    live.start(5000);
    document.title = i18n.t("app.title");
    document.documentElement.lang = i18n.lang;
  });
  onDestroy(() => live.stop());
</script>

<Header />
<Cards />
<CoolingChart />
<PowerChart />
<SettingsModal />
<Toast />

<div class="footer">
  {i18n.t("footer.refresh")} · <a href="https://github.com/Shad107/gpu-dashboard">github</a>
</div>
