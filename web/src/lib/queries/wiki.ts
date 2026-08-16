import { createQuery, createMutation, useQueryClient } from '@tanstack/svelte-query';
import { api, ApiError } from '../api';
import type { PatchWikiPageRequest, PutWikiPageRequest, WikiAccess } from '../types';

export function wikiPagesQuery(projectId: () => string | null) {
  return createQuery(() => ({
    queryKey: ['wiki-pages', projectId()],
    queryFn: () => api.listWikiPages(projectId()!),
    enabled: !!projectId(),
  }));
}

// Cross-project search, optionally narrowed to one project.
export function wikiSearchQuery(
  query: () => string | undefined,
  project?: () => string | undefined,
) {
  return createQuery(() => ({
    queryKey: ['wiki-search', query(), project?.()],
    queryFn: () => api.searchWiki(query()!.trim(), { project: project?.() }),
    enabled: !!query()?.trim(),
  }));
}

export function shareTagsQuery() {
  return createQuery(() => ({
    queryKey: ['share-tags'],
    queryFn: () => api.listShareTags(),
  }));
}

export function wikiPageQuery(projectId: () => string | null, slug: () => string | null) {
  return createQuery(() => ({
    queryKey: ['wiki-page', projectId(), slug()],
    queryFn: () => api.getWikiPage(projectId()!, slug()!),
    enabled: !!projectId() && !!slug(),
    retry: (failureCount, error) => {
      if (error instanceof ApiError && error.status === 404) return false;
      return failureCount < 3;
    },
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

export function patchWikiPageMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (args: { projectId: string; slug: string; req: PatchWikiPageRequest }) =>
      api.patchWikiPage(args.projectId, args.slug, args.req),
    onSuccess: (data, variables) => {
      queryClient.setQueryData(['wiki-page', variables.projectId, variables.slug], data);
      queryClient.invalidateQueries({ queryKey: ['wiki-pages', variables.projectId] });
      queryClient.invalidateQueries({ queryKey: ['wiki-page', variables.projectId, variables.slug] });
      queryClient.invalidateQueries({ queryKey: ['wiki-revisions', variables.projectId, variables.slug] });
    },
  }));
}

export function addShareTagMemberMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (args: {
      tagId: string;
      project: string;
      accessLevel: WikiAccess;
      acknowledgeShare?: boolean;
    }) => api.addShareTagMember(args.tagId, {
      project: args.project,
      access_level: args.accessLevel,
      acknowledge_share: args.acknowledgeShare,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['share-tags'] });
      queryClient.invalidateQueries({ queryKey: ['wiki-pages'] });
    },
  }));
}

export function updateShareTagMemberMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (args: {
      tagId: string;
      project: string;
      accessLevel: WikiAccess;
      acknowledgeShare?: boolean;
    }) => api.updateShareTagMember(args.tagId, args.project, {
      access_level: args.accessLevel,
      acknowledge_share: args.acknowledgeShare,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['share-tags'] });
      queryClient.invalidateQueries({ queryKey: ['wiki-pages'] });
    },
  }));
}

export function removeShareTagMemberMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (args: { tagId: string; project: string }) =>
      api.removeShareTagMember(args.tagId, args.project),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['share-tags'] });
      queryClient.invalidateQueries({ queryKey: ['wiki-pages'] });
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
