<script lang="ts">
  import { toast } from "../lib/stores.svelte";
</script>

<div class="toast {toast.show ? 'show ' + toast.kind : ''}">{toast.message}</div>
