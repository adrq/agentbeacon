<!-- SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

<script lang="ts">
  import type { FieldAnnotation } from '../../../types';
  import RawJsonWidget from './RawJsonWidget.svelte';

  interface Props {
    field: FieldAnnotation;
    value: unknown;
    onchange: (value: unknown) => void;
    onerror?: (hasError: boolean) => void;
  }

  let { field, value, onchange, onerror }: Props = $props();
</script>

<RawJsonWidget {field} {value} {onchange} {onerror} />
