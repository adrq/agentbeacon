// Payload schema support, shared by the renderers.

/** Payload schema versions this client understands. */
export const SUPPORTED_SCHEMA_VERSIONS = new Set([1]);

/**
 * True when an event declares a payload schema this client cannot parse.
 *
 * An absent `schema_version` is version 1.
 */
export function isUnsupportedSchema(event: { schema_version?: number }): boolean {
  return (
    event.schema_version !== undefined &&
    !SUPPORTED_SCHEMA_VERSIONS.has(event.schema_version)
  );
}
