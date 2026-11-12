{
  title: 'PKPass Builder',
  secure_tunnel: true,

  connection: {
    fields: [
      {
        name: 'base_url',
        label: 'API base URL',
        optional: false,
        hint: 'The service origin, for example https://pkpass.example.com. ' \
              'Use HTTPS for production.'
      },
      {
        name: 'api_key',
        label: 'API key',
        control_type: 'password',
        optional: false
      }
    ],
    authorization: {
      type: 'api_key',
      apply: lambda do |connection|
        headers('X-API-Key' => connection['api_key'])
      end
    },
    base_uri: lambda do |connection|
      connection['base_url']
    end
  },

  test: lambda do |_connection|
    get('/health')
  end,

  object_definitions: {
    contact: {
      fields: lambda do |_connection, _config_fields|
        [
          { name: 'givenName', label: 'Given name', optional: false },
          { name: 'surname', label: 'Surname', optional: false },
          { name: 'academicDegree', label: 'Academic degree', optional: false },
          { name: 'jobPosition', label: 'Job position', optional: false },
          { name: 'email', label: 'Email', optional: false },
          { name: 'phoneNumber', label: 'Phone number', optional: false },
          { name: 'department', label: 'Department', optional: false },
          { name: 'location', label: 'Location', optional: false },
          {
            name: 'profilePicture',
            label: 'Profile picture (Base64)',
            optional: true,
            hint: 'Optional PNG, JPEG, or WebP image encoded as Base64; used on Apple Wallet passes only.'
          }
        ]
      end
    }
  },

  actions: {
    create_apple_pass: {
      title: 'Create Apple Wallet pass',
      description: 'Create an Apple Wallet contact pass',
      input_fields: lambda do |object_definitions|
        object_definitions['contact']
      end,
      execute: lambda do |_connection, input|
        contents = post('/passes', input)
                   .headers('Accept' => 'application/vnd.apple.pkpass')
                   .response_format_raw
        {
          'content_base64' => contents.encode_base64,
          'filename' => 'contact.pkpass',
          'content_type' => 'application/vnd.apple.pkpass'
        }
      end,
      output_fields: lambda do |_object_definitions|
        [
          {
            name: 'content_base64',
            label: 'Pass content (Base64)',
            type: 'string',
            hint: 'Decode from Base64 to get the binary .pkpass file.'
          },
          { name: 'filename', label: 'Filename', type: 'string' },
          { name: 'content_type', label: 'Content type', type: 'string' }
        ]
      end
    },
    create_google_wallet_pass: {
      title: 'Create Google Wallet save link',
      description: 'Create a Google Wallet contact pass save link',
      input_fields: lambda do |object_definitions|
        object_definitions['contact']
      end,
      execute: lambda do |_connection, input|
        post('/passes/google-wallet', input)
      end,
      output_fields: lambda do |_object_definitions|
        [
          { name: 'saveUrl', label: 'Save URL', type: 'string' },
          { name: 'objectId', label: 'Object ID', type: 'string' }
        ]
      end
    }
  }
}
