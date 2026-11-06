import brandMarkUrl from '../assets/brand/app-mark.svg';
import brandWordmarkUrl from '../assets/brand/app-wordmark.svg';
import brandWordmarkInverseUrl from '../assets/brand/app-wordmark-inverse.svg';

export const API_URL = window.location.origin;
export const APP_VERSION = 'v8.0.0';
export const BRAND_ACCENT_HEX = '#4F46E5';
export const DEFAULT_MAX_IMAGES_PER_MESSAGE = 10;
export const DEFAULT_MAX_FILES_PER_CONVERSATION = 10;
export const DEFAULT_MAX_BATCH_TOTAL_BYTES = 60 * 1024 * 1024;
export const UPLOAD_POLL_INTERVAL_MS = 1200;
export const UPLOAD_JOB_TIMEOUT_MS = 20 * 60 * 1000;
// Set VITE_DEVOPS_WORKITEM_BASE_URL at build time, e.g. https://dev.azure.com/<org>/<project>/_workitems/edit/
export const DEVOPS_WORKITEM_BASE_URL = import.meta.env?.VITE_DEVOPS_WORKITEM_BASE_URL || '';
export const EMPTY_CONVERSATION = {
  id: null,
  title: 'Nova conversa',
  messages: [],
  mode: 'general',
  uploadedFiles: [],
  pendingUploadJobs: [],
};
export const BRAND_MARK_URL = brandMarkUrl;
export const BRAND_WORDMARK_URL = brandWordmarkUrl;
export const BRAND_WORDMARK_INVERSE_URL = brandWordmarkInverseUrl;
export const BRAND_SYMBOL_DATA_URI = BRAND_MARK_URL;
export const BRAND_LOGO_DARK_TILE_URI = BRAND_MARK_URL;
export const BRAND_LOGO_ACCENT_TILE_URI = BRAND_WORDMARK_URL;
export const BRAND_LOGO_DATA_URI = BRAND_MARK_URL;
