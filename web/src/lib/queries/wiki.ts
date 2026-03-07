import { createQuery, createMutation, useQueryClient } from '@tanstack/svelte-query';
import { api } from '../api';
import type { PutWikiPageRequest } from '../types';

export function wikiPagesQuery(projectId: () => string | null, query?: () => string | undefined) {
  return createQuery(() => ({
    queryKey: ['wiki-pages', projectId(), query?.()],
    queryFn: () => api.listWikiPages(projectId()!, query?.()),
    enabled: !!projectId(),
  }));
}

export function wikiPageQuery(projectId: () => string | null, slug: () => string | null) {
  return createQuery(() => ({
    queryKey: ['wiki-page', projectId(), slug()],
    queryFn: () => api.getWikiPage(projectId()!, slug()!),
    enabled: !!projectId() && !!slug(),
  }));
}

export function wikiRevisionsQuery(projectId: () => string | null, slug: () => string | null) {
  return createQuery(() => ({
    queryKey: ['wiki-revisions', projectId(), slug()],
    queryFn: () => api.listWikiRevisions(projectId()!, slug()!),
    enabled: !!projectId() && !!slug(),
  }));
}

export function wikiRevisionQuery(
  projectId: () => string | null,
  slug: () => string | null,
  rev: () => number | null,
) {
  return createQuery(() => ({
    queryKey: ['wiki-revision', projectId(), slug(), rev()],
    queryFn: () => api.getWikiRevision(projectId()!, slug()!, rev()!),
    enabled: !!projectId() && !!slug() && rev() != null,
  }));
}

export function putWikiPageMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (args: { projectId: string; slug: string; req: PutWikiPageRequest }) =>
      api.putWikiPage(args.projectId, args.slug, args.req),
    onSuccess: (data, variables) => {
      queryClient.setQueryData(['wiki-page', variables.projectId, variables.slug], data);
      queryClient.invalidateQueries({ queryKey: ['wiki-pages', variables.projectId] });
      queryClient.invalidateQueries({ queryKey: ['wiki-page', variables.projectId, variables.slug] });
      queryClient.invalidateQueries({ queryKey: ['wiki-revisions', variables.projectId, variables.slug] });
    },
  }));
}

export function deleteWikiPageMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (args: { projectId: string; slug: string }) =>
      api.deleteWikiPage(args.projectId, args.slug),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ['wiki-pages', variables.projectId] });
      queryClient.invalidateQueries({ queryKey: ['wiki-page', variables.projectId, variables.slug] });
      queryClient.invalidateQueries({ queryKey: ['wiki-revisions', variables.projectId, variables.slug] });
      queryClient.invalidateQueries({ queryKey: ['wiki-revision', variables.projectId, variables.slug] });
    },
  }));
}
