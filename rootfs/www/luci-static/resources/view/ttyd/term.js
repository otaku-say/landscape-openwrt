'use strict';
'require view';
'require uci';

return view.extend({
    load: function() { return uci.load('ttyd'); },
    render: function() {
        var port = uci.get_first('ttyd', 'ttyd', 'port') || '7681';
        var ssl = uci.get_first('ttyd', 'ttyd', 'ssl') || '0';
        var override = uci.get_first('ttyd', 'ttyd', 'url_override');
        if (port === '0')
            return E('div', { class: 'alert-message warning' }, _('A fixed terminal port is required.'));
        var url = new URL(override || ((ssl === '1' ? 'https:' : 'http:') +
            '//' + window.location.hostname + ':' + port), window.location.href);
        var link = E('a', { href: url.href, target: '_blank', rel: 'noopener noreferrer',
            class: 'cbi-button cbi-button-action' }, _('Open terminal'));
        var children = [E('div', { class: 'cbi-page-actions' }, [link])];
        // Browsers block an HTTP terminal iframe in an HTTPS LuCI page.
        if (window.location.protocol !== 'https:' || url.protocol === 'https:')
            children.unshift(E('iframe', { src: url.href, title: _('Terminal'),
                style: 'width:100%;min-height:500px;border:0;resize:vertical;' }));
        return E('div', {}, children);
    },
    handleSaveApply: null,
    handleSave: null,
    handleReset: null
});
